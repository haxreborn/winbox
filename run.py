"""Lançador do HuaweiBox — ponto de entrada para execução direta e
para empacotamento com PyInstaller:

    python run.py
    pyinstaller --onefile --windowed --name "HuaweiWinBox" run.py

O módulo huawei_winbox.main usa imports relativos e por isso não pode
ser passado diretamente como script ao PyInstaller.
"""

from huawei_winbox.main import main

if __name__ == "__main__":
    raise SystemExit(main())
