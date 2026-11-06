"""Programa uma falha no ERP simulado (para mostrar a resiliência na demo).

  python mock_erp/simular.py 503 3       as próximas 3 chamadas respondem HTTP 503
  python mock_erp/simular.py atraso 15   a próxima chamada demora 15 s (estoura o timeout)
  python mock_erp/simular.py sessao      derruba as sessões (força novo login)
  python mock_erp/simular.py normal      volta ao normal
"""
import os
import sys

import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent.parent / '.env')
URL = os.environ.get('ERP_URL', 'http://127.0.0.1:8080').rstrip('/') + '/_simular'

args = sys.argv[1:] or ['normal']
if args[0] == 'atraso':
    corpo = {'atraso': int(args[1]) if len(args) > 1 else 15, 'vezes': 1}
elif args[0] == 'sessao':
    corpo = {'derrubarSessoes': True}
elif args[0] == 'normal':
    corpo = {'vezes': 0}
else:
    corpo = {'status': int(args[0]), 'vezes': int(args[1]) if len(args) > 1 else 3}
print(requests.post(URL, json=corpo, timeout=5).json())
