from nba_api.stats.endpoints import leaguestandingsv3
df = leaguestandingsv3.LeagueStandingsV3().standings.get_data_frame()
print("✅ Fetched standings rows:", len(df))
print(df[["TeamTricode","Conference","ConferenceRank","W","L","W_PCT"]].head(5).to_string(index=False))
