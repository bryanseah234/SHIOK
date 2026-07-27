import sqlite3


def query_samples():
    conn = sqlite3.connect("raw/geocode_cache.db")
    c = conn.cursor()
    c.execute("SELECT postal_code, lat, lon FROM postcodes WHERE status='SUCCESS' LIMIT 3")
    rows = c.fetchall()
    for r in rows:
        print(f"Postal: {r[0]} -> Lat: {r[1]}, Lon: {r[2]}")


if __name__ == "__main__":
    query_samples()
