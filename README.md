# Automação de devoluções de produtos com defeito (ERP MACLE)

### Sobre o Projeto

Automatiza o início do processo de devolução de produtos com defeito, do recebimento do caso até o rascunho do e-mail para a fábrica.

1. **Recebe o caso pelo Telegram** (Usuário): Envio para o bot a foto do defeito com o código do produto na legenda.
2. **Consulta o MACLE**: acha os dados do produto, a nota fiscal de compra, a fábrica e o custo, com uma consulta no ERP.
3. **Aplica as regras de cada fábrica**: prazo de garantia e lote mínimo (em R$ e em pares).
4. **Prepara o e-mail de devolução**: cria o rascunho no Gmail, pronto para o envio.
5. **Avisa no Telegram**: confirma cada caso registrado e manda um resumo.

### Sobre o problema

Hoje, iniciar o processo de devolução de um produto com defeito é mais complicado do que parece. Cada fábrica tem a sua própria política de devolução, e o prazo de garantia e o lote mínimo mudam de uma para outra. Além disso, a fábrica costuma pedir a nota fiscal de origem para comprovar que o produto está dentro do prazo, é preciso saber o e-mail de devolução de cada uma, e não existe um passo a passo documentado por fábrica.

Isso dá trabalho para quem precisa iniciar o processo: a pessoa tem que levantar e conferir todas essas informações à mão, o que abre espaço para o erro humano.

Essa automação visa ajudar quem estiver inciando este processo, pois se o produto estiver cadastrado no sistema, basta o código do produto e numeração, fazendo uma consulta automatizada, filtrando a fábrica a partir dos dados coletados e criando o rascunho no padrão deles.

---

### Arquitetura e fluxo

1. **Gatilho** (`defeitos/main.py`). Um processo só: ouve o bot por long polling e, a cada `INTERVALO_MIN` minutos, faz uma rodada agendada. Cada caso registrado dispara uma rodada na hora.
2. **Recepção** (`recepcao.py`). Lê o código na legenda, confere no MACLE que há um defeito em aberto com aquela referência, baixa a foto, grava o caso e responde no chat.
3. **Consulta no MACLE** (`defeitos/macle/`). A automação faz o mesmo caminho que a pessoa faz na tela e lê o HTML (login, filtro de defeitos, defeito, item, histórico de compras e documento de compra). Cada tela tem uma função de leitura em `telas.py`, e o resultado vai para o `caso.json`.
4. **Triagem** (`triagem.py`). Acha a fábrica pelo CNPJ da NF e decide:
   - sem cadastro ou sem e-mail → **trava**, o caso vai para decisão humana;
   - fora do prazo de garantia (contado da emissão da NF) → **trava**. Vale também para o caso que vence enquanto espera o lote fechar;
   - casos da mesma fábrica formam um **lote**, que só vira e-mail ao atingir o mínimo em R$ e em pares.
5. **E-mail.** O `gmail.py` grava o rascunho, com as fotos anexadas, na pasta Rascunhos por IMAP.
6. **Resumo.** O Telegram recebe o que mudou na rodada, e cada rodada vira uma linha em `logs/execucoes.jsonl`.

---

### Tecnologias

Tecnologias: Python, Docker, unittest.
Serviços: MACLE, Gmail, Telegram (Bot).

---


## Como rodar

**1. Baixar o projeto**

```bash
git clone https://github.com/angelofonseca/automacao-devolucoes-macle.git
cd automacao-devolucoes-macle
```

**2. Criar o ambiente virtual e instalar as dependências**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**3. Criar e preencher o `.env`**

```bash
cp .env.example .env
```

Preencha no `.env`:
- **Gmail**: `GMAIL_USER` com a sua conta e `GMAIL_APP_PASSWORD` com a senha de app.
- **Telegram**: `TELEGRAM_BOT_TOKEN` com o token que o @BotFather dá. Para achar o `TELEGRAM_CHAT_ID`, mande qualquer mensagem ao bot e abra `https://api.telegram.org/bot<TOKEN>/getUpdates`: o número está em `"chat": {"id": ...}`. Só esse chat registra casos e recebe os resumos.
- **MACLE**: para a demo, não precisa de nada. O simulador aceita qualquer login e já vem com a loja 1 (`data/loja.exemplo.json`).

**4. Subir o simulador do MACLE** (num terminal separado, com o ambiente virtual ativo)

```bash
python mock_macle/servidor.py
```


**5. Subir a automação**

```bash
python -m defeitos.main
```

Ela fica ouvindo o bot e, além disso, faz uma rodada a cada `INTERVALO_MIN` minutos (padrão 60) para pegar o que ficou para trás. `Ctrl+C` para parar.

**6. Mandar as fotos ao bot**

No Telegram, abra o chat com o seu bot e mande **uma foto por caso** (qualquer imagem serve na demo) com o **código do produto na legenda**. A legenda aceita dois formatos:
- **item com tamanho**: `loja.item.tamanho`, ex.: `1.2001.40`;
- **código do defeito no MACLE**: `loja.número`, ex.: `1.60000003`.

O primeiro número é sempre a loja. A demo é da **loja 1**: um código de outra loja (ex.: `6.60000003`) recebe a resposta "é da loja 6; esta automação é da loja 1".

Depois do código, se quiser, escreva a descrição do defeito (ex.: `1.2001.40 Costura aberta`). Sem descrição, vale a observação cadastrada no MACLE.

O simulador só conhece os códigos abaixo: são os **únicos defeitos cadastrados no MACLE simulado** (`mock_macle/dados.json`). Um código fora desta lista recebe a resposta "nenhum defeito em aberto", como aconteceria no MACLE real.

| Legenda (item.tamanho) | ou código do defeito | Produto | Fábrica | Qtd. |
|---|---|---|---|---|
| `1.2001.40` | `1.60000003` | Tênis casual PF-880 branco | Passo Firme | 1 |
| `1.2001.42` | `1.60000004` | Tênis casual PF-880 branco | Passo Firme | 2 |
| `1.1001.37` | `1.60000001` | Scarpin AU-2041 preto | Aurora | 1 |
| `1.1002.36` | `1.60000002` | Sandália AU-3310 caramelo | Aurora | 1 |
| `1.1001.38` | `1.60000006` | Scarpin AU-2041 preto | Aurora | 1 |
| `1.1001.36` | `1.60000008` | Scarpin AU-2041 preto | Aurora | 1 |
| `1.3001.39` | `1.60000005` | Bota TR-120 café | Trilha | 1 |
| `1.9001.40` | `1.60000007` | Mocassim MN-01 azul | Marca Nova | 1 |

Cada código só pode ser usado **uma vez**: depois de registrado, o defeito já tem caso. As regras de cada fábrica estão em `data/fornecedores.json`:
- **Passo Firme**: o e-mail sai quando o lote chega a **3 pares**;
- **Aurora**: o e-mail sai quando o lote chega a **R$ 300,00**, e a garantia é de **12 meses da NF**: o que passou disso trava;
- **Trilha**: está cadastrada **sem e-mail**, então o caso trava;
- **Marca Nova**: **não está cadastrada**, então o caso trava.

Roteiro sugerido, nesta ordem:

| Mande as fotos | O que o bot responde |
|---|---|
| 1. `1.2001.40` | caso registrado; lote da Passo Firme espera (1 de 3 pares) |
| 2. `1.2001.42` | lote completo (3 pares): **rascunho criado no Gmail** |
| 3. `1.1001.37` | caso registrado; lote da Aurora espera (R$ 129,90 de R$ 300,00) |
| 4. `1.1002.36` | caso **travado**: fora do prazo (a NF da sandália é de 15/08/2025 e a garantia venceu em 15/08/2026) |
| 5. `1.1001.38` | lote da Aurora ainda espera (R$ 259,80, faltam R$ 40,20) |
| 6. `1.1001.36` | lote da Aurora fecha (R$ 389,70): **rascunho criado** |
| 7. `1.3001.39` | caso **travado**: Trilha sem e-mail de contato |
| 8. `1.9001.40` | caso **travado**: fábrica fora do cadastro de fornecedores |

Os rascunhos ficam na pasta **Rascunhos** do Gmail configurado. Os e-mails das fábricas de exemplo terminam em `.example` e não existem: confira o rascunho, mas não o envie. Para recomeçar a demo do zero, pare a automação e o simulador, apague as pastas `data/entrada/` e `saida/` e suba os dois de novo.

**Com Docker** (no lugar dos passos 2, 4 e 5): depois de preencher o `.env`, rode `docker compose up --build`.

### Casos travados

Caso travado (fora do prazo, fábrica sem cadastro ou sem e-mail, defeito não encontrado no MACLE) fica em `aguardando_decisao`, com o motivo no resumo do Telegram. Depois de resolver (ex.: cadastrar o e-mail da fábrica no `data/fornecedores.json`), reabra o caso para ele voltar à triagem na próxima rodada:

```bash
python -m defeitos.status               # status de todos os casos
python -m defeitos.status --reabrir 5   # o caso 5 volta para a triagem
```

### Usar no MACLE da sua loja

Cada instalação (um bot do Telegram + um `.env`) atende **uma loja**. Para várias lojas, suba uma instalação por loja.

1. **`.env`**: descomente e preencha o login do MACLE em `MACLE_USUARIO`, `MACLE_SENHA` e `MACLE_TERMINAL`. O usuário precisa de permissão nas telas de Defeito, Item e Compras.
2. **`data/loja.json`** (fica fora do git): copie o `data/loja.exemplo.json` e preencha `numero` (a empresa da loja no MACLE, o primeiro número dos códigos), nome, razão social, CNPJ e `servidor`, a URL do MACLE que atende a loja (ex.: `http://IP-DO-SERVIDOR:8080/Controle/`).
3. **`data/fornecedores.json`**: as fábricas com quem você trabalha (CNPJ, e-mail de devolução, prazo, lote mínimo).
4. Rode `python -m defeitos.main` e mande as fotos ao bot.

### Testes

Os testes usam o simulador e um Gmail e um Telegram falsos, então não precisam de rede nem do `.env`:

```bash
python -m unittest discover -s tests -t .
```

**Fidelidade ao MACLE real:** salve páginas do seu MACLE numa pasta fora do repositório (`defeito.html`, `item.html`, `historico.html`, `documento.html`, `lista.html`, `filtro.html`) e rode:

```bash
MACLE_FIXTURES=/caminho/das/paginas python -m unittest tests.test_telas_reais
```

---

## Detalhes técnicos

### Segurança no ERP
O usuário do MACLE pode alterar dados. Por isso a sessão (`macle/sessao.py`) só aceita uma **lista fechada** de endereços e ações:
- só as telas de consulta (defeito, item, histórico e documento de compra) e o filtro de defeitos;
- **devolução, emitir no SEFAZ, cancelar, excluir e carta de correção não estão na lista** e são recusados antes de sair da máquina;
- redirecionamentos só são seguidos para dentro da mesma lista.

Credenciais (MACLE, senha de app do Gmail, token do bot) vêm só do `.env`, que fica fora do git. A `Config` não imprime segredos, e as mensagens de erro passam por `esconder()` antes de ir para o log ou para o Telegram.

### Resiliência e tratamento de erros
| Situação | Comportamento |
|---|---|
| Variável obrigatória faltando no `.env` | não sobe: `[CONFIG] Variáveis faltando no .env: …` |
| Timeout, conexão recusada, HTTP 5xx ou 429 | **retry com backoff** (1 s, 2 s, 4 s…) até `TENTATIVAS`; cada tentativa vai para o log |
| Login do MACLE recusado ou ação fora da lista | **não repete** (repetir não muda o resultado): erro claro na hora |
| Sessão do MACLE expirou | novo login automático e repete a tela uma vez |
| MACLE fora do ar | o bot responde que não conseguiu registrar; casos já registrados ficam para a próxima rodada e o resumo traz "🚨 Falha: MACLE…" |
| Telegram fora do ar | a automação espera 30 s e volta a ouvir; nenhuma foto se perde (o Telegram guarda até ser lida) |
| Código sem defeito em aberto no MACLE | o bot responde pedindo para conferir o código ou cadastrar o defeito |
| Código de outra loja | o bot responde que o código é de outra loja; nada é registrado |
| Reiniciar a automação | a mesma foto não é registrada duas vezes (offset em `saida/telegram_offset.txt`) e o rascunho tem Message-ID fixo por lote |
| Erro inesperado | traceback no log (com segredos mascarados), aviso no Telegram, e o processo continua |

### Melhorias futuras

1. **Acompanhar o envio e a resposta da fábrica** (próximo passo). Detectar no Gmail que a pessoa enviou o e-mail e classificar a resposta da fábrica (autorizou, recusou, pediu dados), com um LLM via API, saída JSON validada por esquema e conferida antes de qualquer ação.
2. **NFD.** Depois da autorização, preparar a nota fiscal de devolução no MACLE e, quando a pessoa emitir, mandar o XML e o DANFE à fábrica.
3. **Painel e fim do ciclo.** Um painel web com os casos parados há mais tempo em cada etapa, incluindo o envio do produto e o ressarcimento da fábrica.
