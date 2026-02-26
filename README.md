# Excel 시트 보호 태그 제거 스크립트

`excel_unprotect.py`는 `.xlsx` 파일 내부 XML의 `<sheetProtection>` 태그를 제거해 시트 보호 상태를 해제합니다.

> ⚠️ 본인 소유 파일 또는 명시적으로 권한을 받은 파일에만 사용하세요.

## 사용법

```bash
python3 excel_unprotect.py 보호된파일.xlsx
```

기본 출력 파일은 `보호된파일_unprotected.xlsx` 입니다.

원본 파일을 백업 후 덮어쓰려면:

```bash
python3 excel_unprotect.py 보호된파일.xlsx --in-place
```

직접 출력 파일명을 지정하려면:

```bash
python3 excel_unprotect.py 보호된파일.xlsx -o 결과파일.xlsx
```
