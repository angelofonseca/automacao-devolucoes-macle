"""Ponto de entrada.

  python -m defeitos.main          ouve o bot do Telegram e faz uma rodada a cada INTERVALO_MIN minutos
  python -m defeitos.main --once   uma rodada e sai, sem ouvir o bot (cron do sistema, teste)

Cada foto registrada no chat dispara uma rodada na hora. A rodada agendada pega o que ficou para trás
(MACLE fora do ar, Gmail sem resposta). Uma falha inesperada é registrada no log e avisada no Telegram;
o processo continua.
"""
import argparse
import sys
import time
import traceback

from . import config, pipeline, recepcao, telegram
from .resiliencia import ErroIntegracao, esconder, registrar

ESPERA_TELEGRAM = 30  # s de long polling: a cada volta confere se já é hora da rodada agendada


def rodar(cfg):
    """Uma rodada protegida: nunca levanta exceção; devolve True se terminou sem falhas."""
    inicio = time.monotonic()
    try:
        r = pipeline.executar(cfg)
    except Exception as exc:
        detalhe = esconder(f'{type(exc).__name__}: {exc}', *cfg.segredos)
        registrar(cfg.log, evento='rodada', ok=False, erro=detalhe,
                  traceback=esconder(traceback.format_exc(), *cfg.segredos))
        print(f'[ERRO] rodada falhou: {detalhe}', file=sys.stderr)
        try:
            telegram.enviar(cfg, f'🚨 Automação de defeitos: a rodada falhou\n{detalhe}')
        except Exception:
            pass  # Telegram também fora: fica só o log
        return False
    registrar(cfg.log, evento='rodada', ok=r['ok'], duracao_s=round(time.monotonic() - inicio, 2),
              rascunhos=len(r['rascunhos']), aguardando=len(r['aguardando']), bloqueados=len(r['bloqueados']),
              invalidos=len(r['invalidos']), falhas=r['falhas'], avisou=r['avisou'])
    print(r['mensagem'])
    print(f"-> Resumo: {'enviado ao Telegram' if r['avisou'] else 'nada novo, sem aviso'}")
    return r['ok']


def ouvir(cfg):
    """Laço principal: long polling no bot + rodada agendada. Sai com Ctrl+C."""
    # O offset guarda até onde já tratamos: reiniciar não registra a mesma foto duas vezes.
    arq_offset = cfg.saida / 'telegram_offset.txt'
    offset = int(arq_offset.read_text()) if arq_offset.exists() else None
    proxima = time.monotonic()  # a primeira rodada é logo ao subir
    print(f'Ouvindo o bot; rodada agendada a cada {cfg.intervalo_min} min (Ctrl+C para parar)')
    try:
        while True:
            if time.monotonic() >= proxima:
                rodar(cfg)
                proxima = time.monotonic() + cfg.intervalo_min * 60
            try:
                atualizacoes = telegram.receber(cfg, offset, espera=ESPERA_TELEGRAM)
            except ErroIntegracao as exc:  # Telegram fora do ar: registra, espera e tenta de novo
                registrar(cfg.log, evento='recepcao', ok=False, erro=str(exc))
                print(f'[ERRO] {exc}; nova tentativa em {ESPERA_TELEGRAM} s', file=sys.stderr)
                time.sleep(ESPERA_TELEGRAM)
                continue
            for u in atualizacoes:
                offset = u['update_id'] + 1
                arq_offset.parent.mkdir(parents=True, exist_ok=True)
                arq_offset.write_text(str(offset))  # antes de atender: uma mensagem que quebra não volta em loop
                msg = u.get('message') or {}
                if str(msg.get('chat', {}).get('id')) != cfg.telegram_chat_id:
                    continue  # só o chat autorizado registra defeitos
                try:
                    caso = recepcao.atender(cfg, msg)
                except Exception as exc:  # nunca derruba o processo: registra e segue
                    detalhe = esconder(f'{type(exc).__name__}: {exc}', *cfg.segredos)
                    registrar(cfg.log, evento='recepcao', ok=False, erro=detalhe)
                    print(f'[ERRO] {detalhe}', file=sys.stderr)
                    continue
                if caso:
                    rodar(cfg)  # caso novo: triagem, rascunho se o lote fechou e resumo no Telegram
    except KeyboardInterrupt:
        return 0


def main(argv=None):
    for fluxo in (sys.stdout, sys.stderr):
        fluxo.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Automação de devoluções de produtos com defeito')
    parser.add_argument('--once', action='store_true', help='executa uma rodada e sai, sem ouvir o bot')
    args = parser.parse_args(argv)
    try:
        cfg = config.carregar()
    except config.ConfigInvalida as exc:
        print(f'[CONFIG] {exc}', file=sys.stderr)
        return 2
    if args.once:
        return 0 if rodar(cfg) else 1
    return ouvir(cfg)


if __name__ == '__main__':
    raise SystemExit(main())
