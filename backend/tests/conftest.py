import sys
from pathlib import Path

# Let tests do `import main`, `import matching`, etc. regardless of where
# pytest is invoked from (repo root or backend/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
