#!/usr/bin/env python3
"""
Demand dashboard updater.
Reads the latest "Advertiser daily this Q" Looker email via Gmail API (gws),
applies the advertiser→team mapping, computes monthly actuals + run rate,
and writes Demand US / Demand TLV into dashboard_data.json.
"""

import json, csv, io, subprocess, base64, datetime, sys
from collections import defaultdict

DIR = "/Users/yariv.sagih/Documents/Claude"
LOG = f"{DIR}/dashboard_update.log"

# ── Team mapping ──────────────────────────────────────────────────────────────
US_ACCOUNTS = {a.lower() for a in [
    "T-Mobile Marketing Solutions (TMS)","ARETIS LIMITED","Scopely_Aura",
    "News Break","Pinterest_AURA","TikTok Aura - 2023","Mobilityware","Chime",
    "Firefox - Mozilla","Lyft","HURELAX PTE. LTD.","TikTok Pte. Ltd. (JP)",
    "Staple Games_Aura","Adobe Express","Indeed","Cash App","Priceline Aura",
    "Azur_Aura Account","Bytedance Pte. Ltd. AURA (APAC)","Tiktok Vodafone (EU)",
    "PubW - AURA","Kooapps-Aura","TubiTV","Exmox Aura",
    "M&C Saatchi Performance (US)","PrizePicks","Circle K (Essence) Aura",
    "Upside-Aura","Pluto TV- Aura","Goodville Aura","Blind Ferret Media",
    "Clearpay","WeatherBug Aura","Best Buy","Kraken","Unico Studio Aura",
    "GoodRx-Aura","Zynga Aura","Rewardify","OMD - NY (aura)","Uber",
    "3Q Pandora Aura","Loom Games","McAfee","SmartNews Aura",
    "Random Logic Games","Booking USD","Meta Platform Technologies, LLC",
    "Paramount +","Bytemode AURA (Europe)","Malwarebytes","Amazon Music 2021",
    "iHeart Radio","Afterpay","FanDuel","GSN Aura US","Life360-Aura","Dave-Aura",
    "Shell via Havas","Niantic","WONDERFUL TYCOON LIMITED",
]}

TLV_ACCOUNTS = {a.lower() for a in [
    "LEARNINGS CO., LIMITED","King Aura","Whaleco Technology Limited (EU)",
    "Tripledot Aura","Apps Innova Limited","PlaySimple Games Pte Ltd Aura",
    "EASYBRAIN LTD","AKRURA PTE. LTD.","Whaleco Services, LLC",
    "Dream Games Aura UK","Playtika Ltd. Aura","Peak Aura",
    "Direct Cursus Technology (Yandex Games)","Shopee AURA","CallApp Aura",
    "JustPlay GmbH Aura","Lion Studios Aura New","Otto Aura","PeopleFun Aura New",
    "Fugo Aura","X-Flow Ltd UA","TOP INCREASE GLOBAL LIMITED","Wooga Aura",
    "Blinkit","MarktGuru Aura","Guru","Good Job games 2025 Aura",
    "Moon Active Aura","Candivore_Aura","Eyecon","Orange Aura Demand",
    "Saygames LTD","WISE WAVE CORPORATION LIMITED","Miniclip Aura",
    "Dentsu London - Next - Aura","Playvalve Aura","Zedge.new",
    "Samsung India Electronics Demand","Funvent Studios DMCC",
    "Plarium Global LTD Aura","JustDice Aura","Blackout Lab SL","Xapads",
    "Big Cake Group Limited","VYBS Aura",
    "IVYMOBILE INTERNATIONAL ENTERPRISE LIMITED","Opera Mini Browser Aura",
    "Superplay Aura","Bytedance C4","Agoda_Aura","FalconStudio",
    "Ilyon Dynamics Aura","Looksoft PL","Wetter.com Aura",
    "ONE97 COMMUNICATIONS LIMITED","Yolo Game Studio Aura",
    "ZIIPIN TECHNOLOGY HK LIMITED","Amundro Global SRL",
    "AppQuantum Publishing LTD","Digital Eagle Aura","MoneyTime Aura",
    "EAGLE,K.K.","Deutsche Telekom Demand","Le figaro","Affinity Global",
    "Drecom - DisneyStep","Burny Games LTD","Huuuge Aura","Newry Aura",
    "Flo Health - Aura","IsCool Aura","Rocket Lab 2025 EU","Besoccer Aura",
    "Mindshare UK Aura - SWR","Rocketads LTD Aura","BILD Aura",
    "Hunter Technology AURA","Square Enix - DoragonQuestWalk",
    "Lessmore Aura","Tencent IEGG","Submarina Ads (P&S)",
    "Wuhan Dobest","Almedia GmbH Aura","JedyApps","Glovo Aura 2022",
    "Spyke Yazılım A.Ş.","Reengagement campaign - KDDI","Tango Aura",
]}

MONTH_MAP = {'2026-04': 'Apr', '2026-05': 'May', '2026-06': 'Jun',
             '2026-07': 'Jul', '2026-08': 'Aug', '2026-09': 'Sep'}

def log(msg):
    with open(LOG, 'a') as f:
        f.write(f"{msg}\n")
    print(msg)


def find_latest_email():
    """Return (thread_id, message_id) of the latest Advertiser daily email."""
    result = subprocess.run(
        ['gws', 'gmail', 'users', 'messages', 'list',
         '--params', json.dumps({"userId": "me", "q": 'subject:"Advertiser daily this Q" in:inbox', "maxResults": 1})],
        capture_output=True, text=True
    )
    data = json.loads('\n'.join(l for l in result.stdout.splitlines() if not l.startswith('Using')))
    msgs = data.get('messages', [])
    if not msgs:
        raise RuntimeError("No 'Advertiser daily this Q' email found in inbox")
    return msgs[0]['threadId'], msgs[0]['id']


def get_attachment_id(message_id):
    """Return the CSV attachment ID by parsing the full message's MIME parts."""
    result = subprocess.run(
        ['gws', 'gmail', 'users', 'messages', 'get',
         '--params', json.dumps({"userId": "me", "id": message_id, "format": "full"})],
        capture_output=True, text=True
    )
    raw = '\n'.join(l for l in result.stdout.splitlines() if not l.startswith('Using'))
    msg = json.loads(raw)

    def search_parts(parts):
        for p in (parts or []):
            fname  = p.get('filename', '')
            att_id = p.get('body', {}).get('attachmentId', '')
            if fname.endswith('.csv') and att_id:
                return att_id
            found = search_parts(p.get('parts', []))
            if found:
                return found
        return None

    att_id = search_parts(msg.get('payload', {}).get('parts', []))
    if not att_id:
        raise RuntimeError("No CSV attachment found")
    return att_id


def download_csv(message_id, attachment_id):
    """Download and decode the CSV attachment, return as text."""
    result = subprocess.run(
        ['gws', 'gmail', 'users', 'messages', 'attachments', 'get',
         '--params', json.dumps({"userId": "me", "messageId": message_id, "id": attachment_id})],
        capture_output=True, text=True
    )
    raw = '\n'.join(l for l in result.stdout.splitlines() if not l.startswith('Using'))
    d = json.loads(raw)
    return base64.urlsafe_b64decode(d['data'] + '==').decode('utf-8', errors='replace')


def process_csv(csv_text, quarter):
    """Parse CSV and return monthly actuals dict per team."""
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    if not rows:
        raise RuntimeError("CSV is empty")

    cols = list(rows[0].keys())
    ac = next(c for c in cols if 'advertiser' in c.lower())
    dc = next(c for c in cols if 'date' in c.lower())
    rc = next(c for c in cols if 'revenue' in c.lower())

    us_m  = defaultdict(float)
    tlv_m = defaultdict(float)
    unmatched = defaultdict(float)

    for r in rows:
        adv   = r[ac].strip()
        month = MONTH_MAP.get(r[dc][:7])
        rev   = float(r[rc].replace('$', '').replace(',', '').strip() or '0')
        if not month or rev == 0:
            continue
        key = adv.lower()
        if   key in US_ACCOUNTS:  us_m[month]  += rev
        elif key in TLV_ACCOUNTS: tlv_m[month] += rev
        else:                     unmatched[month] += rev

    # 60/40 split for unmatched
    for month, v in unmatched.items():
        tlv_m[month] += v * 0.60
        us_m[month]  += v * 0.40

    return us_m, tlv_m


def compute_run_rate(m, today):
    """Compute run rate projection for current month and quarter."""
    cur_month_key = today.strftime('%b')
    days_into = today.day
    days_left_month = (datetime.date(today.year, today.month, 1).replace(
        month=today.month % 12 + 1) - datetime.timedelta(days=1)).day - today.day
    days_left_q = (datetime.date(today.year, 6, 30) - today).days

    cur_actual = m.get(cur_month_key, 0)
    daily_avg  = cur_actual / max(days_into, 1)
    cur_proj   = round(cur_actual + daily_avg * days_left_month)

    completed  = sum(v for k, v in m.items() if k != cur_month_key)
    q_proj     = round(completed + cur_proj)

    rr = {k: round(v) for k, v in m.items()}
    rr[cur_month_key] = cur_proj
    # derive quarter key
    q_keys = {'Apr': 'Q2', 'May': 'Q2', 'Jun': 'Q2',
               'Jul': 'Q3', 'Aug': 'Q3', 'Sep': 'Q3'}
    qk = q_keys.get(cur_month_key, 'Q2')
    rr[qk] = q_proj
    return rr


def main():
    today = datetime.date.today()
    quarter = 'Q2-2026'  # TODO: auto-detect from current date

    log(f"{datetime.datetime.now().isoformat()} — Starting Demand update")

    try:
        thread_id, message_id = find_latest_email()
        att_id = get_attachment_id(message_id)
        csv_text = download_csv(message_id, att_id)
    except Exception as e:
        log(f"ERROR fetching email: {e}")
        sys.exit(1)

    us_m, tlv_m = process_csv(csv_text, quarter)
    us_q  = sum(us_m.values())
    tlv_q = sum(tlv_m.values())

    us_rr  = compute_run_rate(us_m,  today)
    tlv_rr = compute_run_rate(tlv_m, today)

    with open(f"{DIR}/dashboard_data.json") as f:
        data = json.load(f)

    q = data['quarters'][quarter]['teams']
    for team, m, rr, total in [
        ('Demand US',  us_m,  us_rr,  us_q),
        ('Demand TLV', tlv_m, tlv_rr, tlv_q),
    ]:
        for month in list(MONTH_MAP.values()):
            if month in m:
                q[team]['actuals'][month] = round(m[month])
        qk = 'Q2' if today.month <= 6 else 'Q3'
        q[team]['actuals'][qk] = round(total)
        q[team]['runRate'] = rr

    data['quarters'][quarter]['lastUpdated'] = today.isoformat()

    with open(f"{DIR}/dashboard_data.json", 'w') as f:
        json.dump(data, f, indent=2)

    log(f"{today} | SUCCESS | Demand US Q actual: {round(us_q):,} | Run rate EoQ: {us_rr.get('Q2',0):,} | "
        f"Demand TLV Q actual: {round(tlv_q):,} | Run rate EoQ: {tlv_rr.get('Q2',0):,}")


if __name__ == '__main__':
    main()
