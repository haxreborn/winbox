# HuaweiBox

Aplicativo **nativo de desktop em Python** para gerenciar roteadores e
switches **Huawei (VRP)** sem digitar uma linha de comando — inspirado na
simplicidade do **WinBox** da MikroTik.

A ideia central é ser um **tradutor**: você clica na interface gráfica, o
aplicativo gera os comandos CLI do VRP, **mostra o código para você revisar**
e, somente após a confirmação, envia tudo ao equipamento por **SSH**.

## Telas e funcionalidades

- **Tela de conexão estilo WinBox** — endereço, usuário, senha e a lista de
  equipamentos salvos (duplo clique conecta). Os perfis ficam em
  `~/.huaweibox/devices.json`.
- **Casca visual idêntica ao WinBox**: barra de menus
  (Sessão / Configurações / Janelas / Ajuda), barra de ferramentas com
  *Safe Mode*, identificação da sessão e **indicadores de Memória/CPU ao
  vivo** (quadradinho que fica verde/laranja/vermelho conforme a carga),
  faixa vertical do produto e **menu lateral com ícones**. Cada janela
  interna tem barra de ações, caixa **Find** para filtrar a tabela e
  contador de itens no rodapé. Os ícones são desenhados em tempo de
  execução (sem arquivos de imagem).
- **Janela principal com menu lateral + janelas internas (MDI)**, como no
  WinBox. Módulos disponíveis:

  | Módulo | O que faz |
  |---|---|
  | Painel | visão geral ao vivo: CPU, memória, temperatura, banda total, ranking das portas mais carregadas, estado de BGP/OSPF/interfaces e identificação do equipamento — tudo numa janela só |
  | Interfaces | lista com RX/TX ao vivo (vindos do coletor, sem consulta extra), habilitar/desabilitar, descrição, IP, MTU e **Ver no gráfico**; duplo clique abre os detalhes da porta com o gráfico dela embutido |
  | Tráfego (gráfico) | gráfico em tempo real de **várias interfaces ao mesmo tempo**, em modo sobreposto, grade ou empilhado; fonte **SNMP** ou **CLI (SSH)** |
  | Configuração | baixa o `display current-configuration`, navega por blocos, procura texto, **compara com a config salva** (mostra o que se perderia num reboot), tira instantâneos para ver o que mudou, salva em arquivo e envia blocos colados |
  | Endereços IP | visão `display ip interface brief`, adicionar/remover IPs |
  | VLANs | criar/remover VLAN, porta em modo *access* e *trunk* |
  | Rotas | tabela de roteamento, criar/remover rotas estáticas |
  | ARP | tabela IP/MAC, criar/remover entradas estáticas |
  | BGP | vizinhos (`display bgp peer`), habilitar processo, criar/remover peer, anunciar redes |
  | OSPF | vizinhanças (`display ospf peer brief`), habilitar processo, redes por área |
  | MPLS / LDP | sessões LDP, habilitar MPLS+LDP global e por interface |
  | Servidor DHCP | pools (`display ip pool`), criar/remover pool, ativar por interface |
  | PPPoE / BAS | assinantes online (`display access-user`) com filtro e desconexão (`cut access-user`), domínios AAA, pools de assinante e criação de Virtual-Template/acesso BAS — como o *PPP* do WinBox |
  | NAT | NAT de saída (easy-ip por ACL) e redirecionamento de portas (NAT server) |
  | ACL / Firewall | regras permit/deny, aplicar `traffic-filter` nas interfaces |
  | Firewall / NAT | editor rule-by-rule de ACL, traffic-policy (MQC: classifier/behavior/policy) e NAT, com **assistente de CGNAT** (cadeia ACL→redirect ip-nexthop→policy global) usada nos NE8000 |
  | Usuários | contas locais AAA: criar/remover, privilégio e serviços (protege o usuário da sessão) |
  | Vizinhos (LLDP) | descoberta de equipamentos conectados, como o *Neighbors* do WinBox |
  | Serviços | NTP, comunidade SNMP v2c, SSH (STelnet) e LLDP |
  | Logs | eventos do `display logbuffer` em tabela, coloridos por severidade, com filtro por nível/módulo/texto, contadores e modo **Acompanhar** que destaca o que chegou de novo |
  | Ferramentas | ping e traceroute executados a partir do equipamento |
  | Sistema | versão, CPU, memória, uptime, renomear (`sysname`), **salvar configuração** |
  | Novo Terminal | console CLI integrado usando a mesma sessão SSH |

- **Pré-visualização de CLI**: toda ação de configuração abre uma janela
  mostrando exatamente os comandos que serão executados (com `system-view`
  e `return`), com opção de copiar ou cancelar — nada é enviado sem o seu OK.
- **Tudo assíncrono**: as consultas SSH rodam em threads; a janela nunca
  congela.

## Gráfico de tráfego: várias interfaces ao mesmo tempo

O módulo **Tráfego (gráfico)** acompanha quantas portas você quiser de uma
vez. A lista da esquerda marca as interfaces (com filtro, "Ativas" e
"Top 8") e mostra RX/TX/pico de cada uma ao vivo; o quadradinho colorido
liga cada linha do gráfico à sua porta. Três formas de olhar:

| Exibição | Para quê |
|---|---|
| **Sobreposto** | todas no mesmo eixo — comparar quem consome mais |
| **Grade** | um mini-gráfico por porta, no mesmo eixo de tempo — varrer muitas portas sem as linhas se embolarem |
| **Empilhado** | áreas somadas — ver a composição do tráfego total |

Passando o mouse, uma linha vertical mostra o valor de **todas** as séries
naquele mesmo instante. Dá para congelar o desenho (a coleta continua por
trás), escolher a janela de tempo (30 s a "tudo"), ver só RX ou só TX e
exportar o histórico em CSV.

**Marcar mais interfaces não aumenta a carga sobre o equipamento**: cada
amostra já traz todas as portas.

### Fontes de dados

- **SNMP** (recomendada) — lê os contadores de octetos de **todas** as
  interfaces com **GET-BULK**: uma placa de 48 portas cabe em 2 ou 3
  pacotes UDP, contra ~150 idas e voltas do GET-NEXT. É barato o bastante
  para amostrar de segundo em segundo, e a taxa é calculada aqui
  (diferença de octetos ÷ tempo), então o valor é o **instantâneo** de
  verdade. Prefere `ifHCInOctets`/`ifHCOutOctets` (64 bits), cai para os de
  32 bits, e trata a volta do contador sem inventar picos. **Exige o agente
  SNMP habilitado** (use o módulo *SNMP* para isso).
- **CLI (SSH)** — sem agente SNMP, um único `display interface brief` traz
  a utilização de todas as portas; a banda nominal, lida uma vez do dump
  completo, converte porcentagem em bits/s. Continua sendo **um comando por
  amostra**, independente de quantas portas estejam sendo observadas. A
  taxa que o VRP informa é média de 300 s, então é mais suave que a do SNMP.
- **Automático** (padrão) — tenta SNMP e cai para a CLI sozinho.

A comunidade e a porta ficam no próprio painel (ou em
*Configurações → Comunidade SNMP*), e **Testar SNMP** confirma o acesso
lendo `sysName`, `sysUpTime`, `sysDescr` e a contagem de interfaces.

O cliente SNMP v2c é implementado internamente (codificação BER própria),
então **não há dependências novas** — o projeto continua usando apenas
PySide6 e paramiko.

## Como o app se mantém responsivo com muitas janelas abertas

O VRP atende **um** comando por vez em cada sessão SSH. Com várias janelas
em auto-atualização, o antigo "quem chegar primeiro" fazia um
`display interface` demorado segurar a fila inteira — e era isso que fazia
o gráfico engasgar. Agora todo acesso passa por uma fila única
(`core/scheduler.py`) com quatro mecanismos:

- **prioridade** — um clique seu fura a fila das atualizações de fundo;
- **coalescência** — dois painéis pedindo o mesmo `display` ao mesmo tempo
  executam **um** comando e recebem os dois a mesma resposta;
- **cache curto** — saídas de `display` valem alguns segundos, então um
  painel a cada 5 s e outro a cada 10 s dividem a mesma leitura;
- **descarte de pedidos vencidos** — uma atualização de fundo que passou
  tempo demais na fila é jogada fora em vez de executada; quando ela
  finalmente rodasse, o painel já teria pedido a próxima.

Além disso, **um só coletor** (`core/telemetry.py`) alimenta o gráfico, o
Painel e os indicadores da barra — antes cada um consultava o equipamento
por conta própria. Quando nenhuma janela de tráfego está aberta, ele
acompanha apenas CPU e memória.

A barra de status mostra o estado da fila (pedidos pendentes, consultas
evitadas pelo cache/coalescência e descartes), então dá para ver a coisa
funcionando.

## Instalação

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Como executar

```bash
python -m huawei_winbox.main
```

1. Informe o IP, usuário e senha do equipamento Huawei (SSH habilitado com
   `stelnet server enable` e usuário com `service-type ssh`).
2. Clique em **Conectar**. A janela abre no **Painel**, com a visão geral
   do equipamento.
3. Para o gráfico em tempo real de verdade, habilite o agente SNMP (módulo
   **SNMP**) e informe a comunidade em *Configurações → Comunidade SNMP*
   ou no próprio painel de Tráfego. Sem SNMP tudo funciona igual, só com a
   taxa suavizada que a CLI informa.
4. Use o menu lateral para abrir os módulos; cada configuração mostra o CLI
   gerado antes de aplicar.
5. Ao final, use **Sistema → Salvar configuração** para gravar (`save`) —
   ou confira antes no módulo **Configuração** o que está pendente.

## Estrutura do código

```
huawei_winbox/
├── main.py                  # ponto de entrada
├── core/
│   ├── session.py           # sessão SSH persistente (paramiko) com o VRP
│   ├── scheduler.py         # fila de comandos: prioridade, cache, descarte
│   ├── telemetry.py         # coletor único de métricas em tempo real
│   ├── translator.py        # cliques -> comandos CLI do VRP
│   ├── parsers.py           # saída dos "display ..." -> tabelas
│   ├── snmp.py              # cliente SNMP v2c próprio (BER, GET-BULK) — só leitura
│   ├── history.py           # séries temporais em memória (por sessão)
│   └── profiles.py          # equipamentos salvos (~/.huaweibox)
└── ui/
    ├── login.py             # tela de conexão estilo WinBox
    ├── mainwindow.py        # menu lateral + área MDI
    ├── command_preview.py   # pré-visualização do CLI antes do envio
    ├── forms.py             # formulários genéricos
    ├── worker.py            # execução SSH fora da thread da UI
    ├── style.py             # tema visual (QSS)
    ├── widgets/             # gráfico multi-série e sparklines (QPainter)
    └── panels/              # Painel, Interfaces, Tráfego, Configuração, BGP...
```

### Onde os dados ficam

O histórico vive em memória, vinculado à sessão (`WeakKeyDictionary`), e
sobrevive ao fechar/reabrir os painéis. São ~5400 amostras por série
(90 min a 1 s, 7h30 a 5 s) guardadas em `array('d')` circular: 48 portas
ocupam cerca de 6 MB. Nada é gravado em disco — para levar embora, use
*CSV…* no gráfico ou *Salvar em arquivo…* na Configuração.

## Avisos

- O aplicativo força um tema claro próprio (estilo Fusion + paleta fixa),
  então os textos permanecem legíveis mesmo com o sistema operacional em
  modo escuro.

- Testado contra a sintaxe do VRP clássico (séries AR, NE, S) e contra a
  V800 (NE8000), que usa configuração em dois estágios (a sessão envia
  `commit` automaticamente quando o prompt aparece como `[~nome]`/`[*nome]`).
  Versões diferentes podem variar em alguns `display`; os parsers ignoram
  linhas desconhecidas em vez de quebrar.
- **Login com aviso de senha**: alguns equipamentos exibem, logo após o
  SSH, `The password needs to be changed. Change now? [Y/N]:`. O
  aplicativo responde **N** automaticamente (segue para o prompt sem trocar
  a senha). As demais confirmações `[Y/N]` (como `save`) continuam sendo
  respondidas com **Y**.
- A opção "Lembrar senha" grava a senha apenas ofuscada (base64) no disco;
  em ambientes sensíveis, deixe desmarcada.
- Configurações aplicadas **não são gravadas na flash automaticamente** —
  use *Salvar configuração* no módulo Sistema. O módulo **Configuração**
  tem o botão *Comparar com a salva*, que lista exatamente o que ainda
  está só na memória e se perderia num reboot.
- O painel **Configuração** também vigia alterações: marque um instantâneo
  e escolha um intervalo em *Vigiar mudanças* para ser avisado quando a
  configuração mudar — inclusive por outra pessoa, em outra sessão.
