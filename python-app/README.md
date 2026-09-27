# Python App

標準ライブラリだけで動くPythonプロジェクトのスターターです。

## 実行

```bash
cd python-app
PYTHONPATH=src python -m python_app
PYTHONPATH=src python -m python_app --name Hana
PYTHONPATH=src python -m python_app --name Hana --json
```

## インストールして使う場合

```bash
cd python-app
python -m pip install -e .
python-app --name Hana
```

## テスト

```bash
cd python-app
PYTHONPATH=src python -m unittest discover -s tests -v
```
