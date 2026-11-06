"""Registra um caso novo, como a recepção faz quando o gerente da loja manda código + foto.

  python -m defeitos.novo_caso --loja 1 --codigo 1.1001.38 --defeito "Salto soltando" [--quantidade 1] [--foto foto.jpg]
  python -m defeitos.novo_caso --reset      apaga data/entrada/ e recria os casos de data/exemplos/casos.json

Sem --foto, gera uma imagem de exemplo para o e-mail ter anexo.
"""
import argparse
import json
import shutil
import struct
import sys
import zlib
from datetime import datetime
from pathlib import Path

from . import casos as repo
from .config import RAIZ

EXEMPLOS = RAIZ / 'data' / 'exemplos' / 'casos.json'


def png_exemplo(largura=120, altura=80, cor=(200, 60, 60)):
    """PNG simples de uma cor só, gerado sem biblioteca externa (placeholder da foto)."""
    def bloco(tipo, dados):
        return struct.pack('>I', len(dados)) + tipo + dados + struct.pack('>I', zlib.crc32(tipo + dados))
    linha = b'\x00' + bytes(cor) * largura
    return (b'\x89PNG\r\n\x1a\n' + bloco(b'IHDR', struct.pack('>IIBBBBB', largura, altura, 8, 2, 0, 0, 0))
            + bloco(b'IDAT', zlib.compress(linha * altura)) + bloco(b'IEND', b''))


def criar(entrada, loja, codigo, quantidade, defeito, foto=None):
    if foto and Path(foto).suffix.lower() not in ('.jpg', '.jpeg', '.png', '.webp'):
        raise ValueError('foto precisa ser .jpg, .jpeg, .png ou .webp')
    protocolo = repo.proximo_protocolo(entrada)
    pasta = Path(entrada) / protocolo
    pasta.mkdir(parents=True)
    if foto:
        nome = f'foto1{Path(foto).suffix.lower()}'
        shutil.copyfile(foto, pasta / nome)
    else:
        nome = 'foto1.png'
        (pasta / nome).write_bytes(png_exemplo())
    caso = {'protocolo': protocolo, 'loja': str(loja), 'codigo': codigo, 'quantidade': quantidade,
            'defeito': defeito, 'fotos': [nome], 'status': 'novo', 'recebidoEm': datetime.now().isoformat(timespec='seconds')}
    erros = repo.validar(caso)
    if erros:
        shutil.rmtree(pasta)
        raise ValueError('; '.join(erros))
    repo.gravar(pasta, caso)
    return caso


def resetar(entrada):
    if Path(entrada).exists():
        shutil.rmtree(entrada)
    exemplos = json.loads(EXEMPLOS.read_text(encoding='utf-8'))['casos']
    return [criar(entrada, **e) for e in exemplos]


def main(argv=None):
    sys.stdout.reconfigure(encoding='utf-8')
    p = argparse.ArgumentParser(description='Registra um caso de defeito')
    p.add_argument('--reset', action='store_true', help='recria os casos de exemplo')
    p.add_argument('--loja')
    p.add_argument('--codigo')
    p.add_argument('--quantidade', type=int, default=1)
    p.add_argument('--defeito')
    p.add_argument('--foto')
    p.add_argument('--entrada', default=str(RAIZ / 'data' / 'entrada'))
    a = p.parse_args(argv)
    if a.reset:
        for c in resetar(a.entrada):
            print(f"caso #{c['protocolo']}: loja {c['loja']}, {c['codigo']}, {c['defeito']}")
        return 0
    if not (a.loja and a.codigo and a.defeito):
        p.error('informe --loja, --codigo e --defeito (ou --reset)')
    c = criar(a.entrada, a.loja, a.codigo, a.quantidade, a.defeito, a.foto)
    print(f"caso #{c['protocolo']} registrado (status novo)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
