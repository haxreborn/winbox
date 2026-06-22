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
  | Interfaces | lista (`display interface brief`), habilitar/desabilitar, descrição, IP, MTU |
  | Tráfego (gráfico) | gráfico em tempo real de consumo de banda (RX/TX) por interface, com histórico do tempo em que a janela está aberta; fonte **CLI (SSH)** ou **SNMP** |
  | Endereços IP | visão `display ip interface brief`, adicionar/remover IPs |
  | VLANs | criar/remover VLAN, porta em modo *access* e *trunk* |
  | Rotas | tabela de roteamento, criar/remover rotas estáticas |
  | ARP | tabela IP/MAC, criar/remover entradas estáticas |
  | BGP | vizinhos (`display bgp peer`), habilitar processo, criar/remover peer, anunciar redes |
  | OSPF | vizinhanças (`display ospf peer brief`), habilitar processo, redes por área |
  | MPLS / LDP | sessões LDP, habilitar MPLS+LDP global e por interface |
  | Servidor DHCP | pools (`display ip pool`), criar/remover pool, ativar por interface |
  | NAT | NAT de saída (easy-ip por ACL) e redirecionamento de portas (NAT server) |
  | ACL / Firewall | regras permit/deny, aplicar `traffic-filter` nas interfaces |
  | Usuários | contas locais AAA: criar/remover, privilégio e serviços (protege o usuário da sessão) |
  | Vizinhos (LLDP) | descoberta de equipamentos conectados, como o *Neighbors* do WinBox |
  | Serviços | NTP, comunidade SNMP v2c, SSH (STelnet) e LLDP |
  | Logs | `display logbuffer` com filtro por texto |
  | Ferramentas | ping e traceroute executados a partir do equipamento |
  | Sistema | versão, CPU, memória, uptime, renomear (`sysname`), **salvar configuração** |
  | Novo Terminal | console CLI integrado usando a mesma sessão SSH |

- **Pré-visualização de CLI**: toda ação de configuração abre uma janela
  mostrando exatamente os comandos que serão executados (com `system-view`
  e `return`), com opção de copiar ou cancelar — nada é enviado sem o seu OK.
- **Tudo assíncrono**: as consultas SSH rodam em threads; a janela nunca
  congela.

## Gráfico de tráfego e SNMP

O módulo **Tráfego (gráfico)** desenha o consumo de banda (RX/TX) da
interface escolhida enquanto a janela fica aberta — um gráfico estilo
*Traffic* do WinBox, com escala automática e legenda de valor atual/pico.
O histórico fica em memória (vinculado à sessão) e sobrevive ao fechar e
reabrir o painel. Há duas fontes de dados:

- **CLI (SSH)** — lê a taxa que o próprio equipamento informa
  (`display interface <nome>`). Funciona sem nenhuma configuração extra
  além do SSH.
- **SNMP** — lê os contadores de octetos (`ifHCInOctets`/`ifHCOutOctets`,
  com queda para os de 32 bits) e calcula a taxa entre as amostras. **Exige
  que o agente SNMP já esteja habilitado no equipamento** (use o módulo
  *SNMP* para isso); a comunidade e a porta são informadas no próprio
  painel, e o botão **Testar SNMP** confirma o acesso lendo `sysName`,
  `sysUpTime` e `sysDescr`.

O cliente SNMP v2c é implementado internamente (codificação BER própria),
então **não há dependências novas** — o projeto continua usando apenas
PySide6 e paramiko.

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
2. Clique em **Conectar**.
3. Use o menu lateral para abrir os módulos; cada configuração mostra o CLI
   gerado antes de aplicar.
4. Ao final, use **Sistema → Salvar configuração** para gravar (`save`).

## Estrutura do código

```
huawei_winbox/
├── main.py                  # ponto de entrada
├── core/
│   ├── session.py           # sessão SSH persistente (paramiko) com o VRP
│   ├── translator.py        # cliques -> comandos CLI do VRP
│   ├── parsers.py           # saída dos "display ..." -> tabelas
│   ├── snmp.py              # cliente SNMP v2c próprio (BER) — só leitura
│   ├── history.py           # histórico de tráfego em memória (por sessão)
│   └── profiles.py          # equipamentos salvos (~/.huaweibox)
└── ui/
    ├── login.py             # tela de conexão estilo WinBox
    ├── mainwindow.py        # menu lateral + área MDI
    ├── command_preview.py   # pré-visualização do CLI antes do envio
    ├── forms.py             # formulários genéricos
    ├── worker.py            # execução SSH fora da thread da UI
    ├── style.py             # tema visual (QSS)
    ├── widgets/             # widgets reutilizáveis (gráfico de tráfego)
    └── panels/              # Interfaces, Tráfego, VLANs, BGP, OSPF, MPLS...
```

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
  use *Salvar configuração* no módulo Sistema.
