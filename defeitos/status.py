"""Marca o que a pessoa fez fora da automação: enviou o e-mail, a fábrica autorizou, decidiu um caso travado.

  python -m defeitos.status --protocolos 3,4 --para enviado      revisou e enviou o rascunho do Gmail
  python -m defeitos.status --protocolos 3,4 --para autorizado   a fábrica autorizou -> próxima rodada monta a NFD
  python -m defeitos.status --protocolos 5 --para novo           decisão tomada: volta para a triagem
  python -m defeitos.status --listar                             status de todos os casos
"""
import argparse
import sys

from . import casos as repo
from .config import RAIZ


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description='Muda o status de casos (só transições permitidas)')
    p.add_argument('--protocolos', help='protocolos separados por vírgula')
    p.add_argument('--para', choices=sorted(repo.TRANSICOES))
    p.add_argument('--motivo', default='marcado pela pessoa')
    p.add_argument('--listar', action='store_true')
    p.add_argument('--entrada', default=str(RAIZ / 'data' / 'entrada'))
    a = p.parse_args(argv)
    from pathlib import Path
    todos = {c['protocolo']: (pasta, c) for pasta, c, erros in repo.listar(Path(a.entrada)) if c and not erros}
    if a.listar or not (a.protocolos and a.para):
        for prot, (_, c) in todos.items():
            print(f"#{prot:>3}  {c['status']:<22} loja {c['loja']}  {c['codigo']}  {c.get('bloqueio', '')}")
        return 0
    erro = 0
    for prot in [x.strip().lstrip('#') for x in a.protocolos.split(',') if x.strip()]:
        if prot not in todos:
            print(f'#{prot}: caso não encontrado')
            erro = 1
            continue
        pasta, c = todos[prot]
        try:
            if a.para == 'novo':
                c.pop('bloqueio', None)
            repo.mudar_status(c, a.para, a.motivo)
            repo.gravar(pasta, c)
            print(f"#{prot}: {c['historico'][-1]['de']} -> {a.para}")
        except repo.TransicaoInvalida as exc:
            print(exc)
            erro = 1
    return erro


if __name__ == '__main__':
    raise SystemExit(main())
