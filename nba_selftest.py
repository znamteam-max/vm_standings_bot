import sys, time, traceback
from requests.exceptions import HTTPError, ReadTimeout, ConnectionError as ReqConnError

print("🔧 Importing nba_api ...", flush=True)
try:
    from nba_api.stats.endpoints import leaguestandingsv3
except Exception as e:
    print("❌ Не удалось импортировать nba_api:", e)
    sys.exit(1)

def fetch_once():
    df = leaguestandingsv3.LeagueStandingsV3().standings.get_data_frame()
    # sanity-вывод
    cols = ["TeamTricode","Conference","ConferenceRank","W","L","W_PCT"]
    print("✅ Rows:", len(df))
    print(df[cols].head(10).to_string(index=False))

# ретраи с понятными сообщениями (если CDN/NBA капризничает)
last_err = None
for i in range(1, 5):
    try:
        print(f"⏩ Попытка {i}/4 ...", flush=True)
        fetch_once()
        print("✅ NBA fetch OK")
        sys.exit(0)
    except (HTTPError, ReadTimeout, ReqConnError) as e:
        last_err = e
        print(f"⚠️  Cетевой сбой от NBA (attempt {i}/4): {e}")
        time.sleep(3)
    except Exception as e:
        last_err = e
        print("❌ Неожиданная ошибка при запросе standings:")
        traceback.print_exc()
        break

print(f"❌ Итог: не удалось получить standings: {last_err}")
sys.exit(1)
