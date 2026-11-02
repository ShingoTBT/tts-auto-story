"""二重投稿防止と「遅れて届いた予備の定時起動」の判定(TikTok 動画のワークフロー3本で共通)。

2026-11-02〜 GitHub の定時起動(予備)は1〜4時間遅れて届き、90分の確認をすり抜けて余分な投稿になり、
定刻の Xserver の回を止めていた(11時の回が消える・解説の枠がずれる)。そこで:
  - Xserver・手動の起動(workflow_dispatch): 今までどおり、直近90分以内に投稿していれば何もしない
  - 予備の定時起動(schedule): 自分の枠の時刻以降に投稿があれば何もしない(定刻の回が済んでいる)。
    次の枠まで90分を切っていても何もしない(定刻の次の回を止めないため)。
    どちらでもなければ(定刻の回が失敗していた)、その枠の分として投稿する
  - force(臨時の投稿): 何も止めない
枠の時刻は起動した時刻ではなく元の予定(cron)で決め、slot= に出す(解説の枠の判定に使う)。

使い方: python schedule_guard.py <アカウント設定> <枠(JST, 例 1,6,11,16,21)>
  環境変数 EVENT_NAME(github.event_name)・SCHEDULE(github.event.schedule)・FORCE(inputs.force)
  GITHUB_OUTPUT に skip=true/false と slot=<時>
"""
import datetime
import json
import os
import sys

import yaml

JST = datetime.timezone(datetime.timedelta(hours=9))
GUARD_MINUTES = 90
# Xserver の合図がわずかに早く届いた投稿も「この枠の投稿」と数える余裕
SLOT_MARGIN_MINUTES = 10


def last_slot_before(now, slots):
    """now(JST)以前で一番近い枠の時刻。"""
    cands = []
    for day in (0, 1):
        base = (now - datetime.timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        for h in slots:
            t = base.replace(hour=h)
            if t <= now:
                cands.append(t)
    return max(cands)


def next_slot_after(t, slots):
    for day in (0, 1):
        base = (t + datetime.timedelta(days=day)).replace(minute=0, second=0, microsecond=0)
        for h in sorted(slots):
            c = base.replace(hour=h)
            if c > t:
                return c
    raise ValueError("no next slot")


def slot_from_cron(cron, now):
    """"0 16 * * *"(UTC) → now(JST)以前で一番近いその時刻(JST)。"""
    minute, hour = cron.split()[:2]
    jst_hour = (int(hour) + 9) % 24
    t = now.replace(hour=jst_hour, minute=int(minute), second=0, microsecond=0)
    if t > now:
        t -= datetime.timedelta(days=1)
    return t


def decide(posted_times, now, slots, event, schedule="", force=False):
    """(skip, slot_time, 理由) を返す。posted_times は JST の datetime の一覧。"""
    if event == "schedule" and schedule:
        slot_time = slot_from_cron(schedule, now)
    else:
        slot_time = last_slot_before(now, slots)
    if force:
        return False, slot_time, "force"
    last = max(posted_times) if posted_times else None
    if last and (now - last).total_seconds() / 60 < GUARD_MINUTES:
        return True, slot_time, f"last post {(now - last).total_seconds() / 60:.0f} minutes ago (< {GUARD_MINUTES})"
    if event == "schedule":
        since = slot_time - datetime.timedelta(minutes=SLOT_MARGIN_MINUTES)
        done = [t for t in posted_times if t >= since]
        if done:
            return True, slot_time, f"slot {slot_time:%m-%d %H:%M} already posted at {min(done):%H:%M} (late backup run)"
        nxt = next_slot_after(slot_time, slots)
        if (nxt - now).total_seconds() / 60 < GUARD_MINUTES:
            return True, slot_time, f"next slot {nxt:%H:%M} is within {GUARD_MINUTES} minutes (leave it to the on-time run)"
        return False, slot_time, f"slot {slot_time:%m-%d %H:%M} has no post yet (backup run fills it)"
    return False, slot_time, "no recent post"


def load_posted_times(config_path):
    cfg = yaml.safe_load(open(config_path, encoding="utf-8"))
    path = cfg["output_dir"] + "/posted_videos.jsonl"
    times = []
    for line in open(path, encoding="utf-8"):
        if line.strip():
            times.append(datetime.datetime.fromisoformat(json.loads(line)["posted_at"]).astimezone(JST))
    return times


def main():
    config_path = sys.argv[1]
    slots = [int(h) for h in sys.argv[2].split(",")]
    now = datetime.datetime.now(JST)
    event = os.environ.get("EVENT_NAME", "")
    schedule = os.environ.get("SCHEDULE", "")
    force = os.environ.get("FORCE") == "true"
    try:
        posted = load_posted_times(config_path)
    except Exception as e:
        # 記録が読めないときは今までどおり止めない
        print(f"guard check failed, continue: {e}")
        posted = []
    skip, slot_time, reason = decide(posted, now, slots, event, schedule, force)
    print(f"now {now:%m-%d %H:%M} JST event={event} schedule={schedule!r} → skip={skip}: {reason}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            print("skip=" + ("true" if skip else "false"), file=f)
            print(f"slot={slot_time.hour}", file=f)


if __name__ == "__main__":
    main()
