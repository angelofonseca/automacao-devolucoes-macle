"""Consulta e reabre casos.

  python -m defeitos.status                 status de todos os casos
  python -m defeitos.status --reabrir 5     caso travado (aguardando_decisao) volta para a triagem,
                                            ex.: depois de cadastrar o e-mail da fábrica no fornecedores.json
"""
import argparse
import sys
from pathlib import Path

from . import casos as repo
from .config import RAIZ


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description='Lista os casos ou reabre um caso travado')
    p.add_argument('--reabrir', metavar='PROTOCOLOS', help='protocolos separados por vírgula')
    p.add_argument('--motivo', default='reaberto pela pessoa')
    p.add_argument('--entrada', default=str(RAIZ / 'data' / 'entrada'))
    a = p.parse_args(argv)
    todos = {c['protocolo']: (pasta, c) for pasta, c, erros in repo.listar(Path(a.entrada)) if c and not erros}
    if not a.reabrir:
        for prot, (_, c) in todos.items():
            print(f"#{prot:>3}  {c['status']:<22} loja {c['loja']}  {c['codigo']}  {c.get('bloqueio', '')}")
        return 0
    erro = 0
    for prot in [x.strip().lstrip('#') for x in a.reabrir.split(',') if x.strip()]:
        if prot not in todos:
            print(f'#{prot}: caso não encontrado')
            erro = 1
            continue
        pasta, c = todos[prot]
        try:
            repo.mudar_status(c, 'novo', a.motivo)
        except repo.TransicaoInvalida as exc:
            print(exc)
            erro = 1
            continue
        c.pop('bloqueio', None)
        repo.gravar(pasta, c)
        print(f"#{prot}: {c['historico'][-1]['de']} -> novo")
    return erro


if __name__ == '__main__':
    raise SystemExit(main())
