"""Casos de defeito em disco: uma pasta por protocolo com caso.json e as fotos.

O status só muda por mudar_status(), que confere se a transição é permitida e guarda o histórico.
"""
import json
import os
from datetime import datetime
from pathlib import Path

# De onde cada status pode ir. "enviado" e "autorizado" são marcados pela pessoa (python -m defeitos.status).
TRANSICOES = {
    'novo': {'aguardando_lote', 'aguardando_decisao', 'aguardando_aprovacao'},
    'aguardando_lote': {'aguardando_decisao', 'aguardando_aprovacao'},
    'aguardando_decisao': {'novo'},
    'aguardando_aprovacao': {'enviado'},            # a pessoa revisou e enviou o e-mail
    'enviado': {'autorizado', 'aguardando_decisao'},  # a fábrica autorizou (ou recusou) a devolução
    'autorizado': {'nfd_rascunho', 'aguardando_decisao'},
    'nfd_rascunho': set(),                          # a pessoa confere e emite a NFD na tela do MACLE
}
EM_TRIAGEM = {'novo', 'aguardando_lote'}
CAMPOS = {'protocolo': str, 'loja': str, 'codigo': str, 'quantidade': int, 'defeito': str, 'status': str}


class TransicaoInvalida(Exception):
    pass


def validar(caso):
    """Lista do que está errado no caso.json (vazia = válido)."""
    if not isinstance(caso, dict):
        return ['caso.json não é um objeto']
    erros = [f'campo {c} ausente ou não é {t.__name__}' for c, t in CAMPOS.items() if not isinstance(caso.get(c), t)]
    if isinstance(caso.get('quantidade'), int) and caso['quantidade'] < 1:
        erros.append('quantidade precisa ser >= 1')
    if caso.get('status') not in TRANSICOES:
        erros.append(f"status desconhecido: {caso.get('status')!r}")
    return erros


def listar(entrada):
    """(pasta, caso, erros) de cada caso, em ordem de protocolo. Arquivo ilegível não derruba a rodada."""
    if not entrada.exists():
        return []
    saida = []
    for arq in sorted(entrada.glob('*/caso.json'), key=lambda p: (len(p.parent.name), p.parent.name)):
        try:
            caso = json.loads(arq.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as exc:
            saida.append((arq.parent, None, [f'caso.json ilegível: {exc}']))
            continue
        saida.append((arq.parent, caso, validar(caso)))
    return saida


def gravar(pasta, caso):
    """Gravação atômica: escreve num temporário e troca. Queda no meio não deixa caso.json pela metade."""
    tmp = pasta / 'caso.json.tmp'
    tmp.write_text(json.dumps(caso, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, pasta / 'caso.json')


def mudar_status(caso, novo, motivo):
    atual = caso['status']
    if novo == atual:
        return False
    if novo not in TRANSICOES.get(atual, set()):
        raise TransicaoInvalida(f"caso {caso['protocolo']}: {atual} -> {novo} não é permitido")
    caso['status'] = novo
    caso.setdefault('historico', []).append(
        {'em': datetime.now().isoformat(timespec='seconds'), 'de': atual, 'para': novo, 'motivo': motivo})
    return True


def proximo_protocolo(entrada):
    numeros = [int(p.name) for p in Path(entrada).glob('*') if p.is_dir() and p.name.isdigit()]
    return str(max(numeros, default=0) + 1)
