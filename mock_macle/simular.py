"""Programa uma falha no simulador do MACLE (para mostrar a resiliência na demo).

  python mock_macle/simular.py 503 3       as próximas 3 chamadas respondem HTTP 503
  python mock_macle/simular.py atraso 20   a próxima chamada demora 20 s (estoura o HTTP_TIMEOUT)
  python mock_macle/simular.py sessao      derruba as sessões (a automação faz login de novo)
  python mock_macle/simular.py normal      volta ao normal
"""
import sys

import requests

URL = 'http://127.0.0.1:8080/_simular'

args = sys.argv[1:] or ['normal']
if args[0] == 'atraso':
    corpo = {'atraso': int(args[1]) if len(args) > 1 else 20, 'vezes': 1}
elif args[0] == 'sessao':
    corpo = {'derrubarSessoes': True}
elif args[0] == 'normal':
    corpo = {'vezes': 0}
else:
    corpo = {'status': int(args[0]), 'vezes': int(args[1]) if len(args) > 1 else 3}
print(requests.post(URL, json=corpo, timeout=5).json())
