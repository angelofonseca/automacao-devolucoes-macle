"""Casos de defeito em disco: uma pasta por protocolo com caso.json e as fotos.

O status só muda por mudar_status(), que confere se a transição é permitida e guarda o histórico.
"""
import json
import os
import shutil
from datetime import datetime
from pathlib import Path

# De onde cada status pode ir. O fluxo termina no rascunho do e-mail: a pessoa revisa e envia pelo Gmail.
# Um caso travado volta para a triagem quando a pessoa resolve (python -m defeitos.status --reabrir N).
TRANSICOES = {
    'novo': {'aguardando_lote', 'aguardando_decisao', 'aguardando_aprovacao'},
    'aguardando_lote': {'aguardando_decisao', 'aguardando_aprovacao'},
    'aguardando_decisao': {'novo'},
    'aguardando_aprovacao': set(),  # fim: rascunho criado no Gmail
}
EM_TRIAGEM = {'novo', 'aguardando_lote'}
FOTOS = ('.jpg', '.jpeg', '.png', '.webp')
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


def criar(entrada, loja, codigo, quantidade, defeito, foto):
    """Registra um caso novo com a foto recebida. Devolve o caso gravado (status novo)."""
    foto = Path(foto)
    if foto.suffix.lower() not in FOTOS:
        raise ValueError('foto precisa ser ' + ', '.join(FOTOS))
    if not foto.is_file():  # antes de criar a pasta: senão sobra uma pasta vazia e o protocolo se perde
        raise ValueError(f'foto não encontrada: {foto}')
    protocolo = proximo_protocolo(entrada)
    pasta = Path(entrada) / protocolo
    pasta.mkdir(parents=True)
    nome = f'foto1{foto.suffix.lower()}'
    shutil.copyfile(foto, pasta / nome)
    caso = {'protocolo': protocolo, 'loja': str(loja), 'codigo': codigo, 'quantidade': quantidade,
            'defeito': defeito, 'fotos': [nome], 'status': 'novo', 'recebidoEm': datetime.now().isoformat(timespec='seconds')}
    erros = validar(caso)
    if erros:
        shutil.rmtree(pasta)
        raise ValueError('; '.join(erros))
    gravar(pasta, caso)
    return caso
