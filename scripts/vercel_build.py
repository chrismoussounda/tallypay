import os
import subprocess
import sys

# Previews must not change the live database.
if os.environ.get("VERCEL_ENV") == "production":
    subprocess.run([sys.executable, "manage.py", "migrate", "--noinput"], check=True)
