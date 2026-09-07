import json
import urllib.parse
import urllib.request

H = {"User-Agent": "MTGManagement/1.0", "Accept": "application/json"}


def q(query, order="edhrec", n=60):
    u = "https://api.scryfall.com/cards/search?" + urllib.parse.urlencode(
        {"q": query, "order": order, "dir": "asc", "unique": "cards"}
    )
    try:
        d = json.load(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20))
        return [(c["name"], c.get("mana_cost", ""), c["type_line"], c.get("set", "")) for c in d.get("data", [])][:n]
    except Exception as e:
        return ["ERR " + str(e)]


print("=== GW spot removal ===")
for row in q("(set:ltr or set:ltc or set:hob) id<=gw (type:instant or type:sorcery) (oracle:exile or oracle:destroy) -oracle:\"destroy all\"", "cmc", 30):
    print(row)
print("=== GW wipes / mass ===")
for row in q("(set:ltr or set:ltc or set:hob) id<=gw (oracle:\"destroy all\" or oracle:\"exile all\" or oracle:\"each creature\")", "cmc", 20):
    print(row)
print("=== GW lands (ltr/ltc) fixing ===")
for row in q("(set:ltr or set:ltc) type:land -type:basic id<=gw", "name", 30):
    print(row)
print("=== GW ramp/draw staples mv<=3 ===")
for row in q("(set:ltr or set:ltc or set:hob) id<=gw (oracle:add or oracle:\"search your library for a\" or oracle:draw) (type:artifact or type:creature) mv<=3", "edhrec", 30):
    print(row)
