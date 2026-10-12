import sys
from pathlib import Path

# 어느 폴더에서 pytest 를 실행해도 vedit 패키지를 찾도록
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
