import os
from urllib.parse import urlsplit

u = os.environ.get("TEST_DATABASE_URL", "")
p = urlsplit(u)
host = p.hostname or ""
print("set hai:", bool(u))
print("scheme:", p.scheme)
print("'@' kitni baar:", u.count("@"))
print("'...' hai:", "..." in u)
print("host ke hisse:", len(host.split(".")), [len(x) for x in host.split(".")])
print("db naam mein 'test':", "test" in p.path)