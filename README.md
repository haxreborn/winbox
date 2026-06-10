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
- **Janela principal com menu lateral + janelas internas (MDI)**, como no
  WinBox. Módulos disponíveis:

  | Módulo | O que faz |
  |---|---|
  | Interfaces | lista (`display interface brief`), habilitar/desabilitar, descrição, IP, MTU |
  | Endereços IP | visão `display ip interface brief`, adicionar/remover IPs |
  | VLANs | criar/remover VLAN, porta em modo *access* e *trunk* |
  | Rotas | tabela de roteamento, criar/remover rotas estáticas |
  | BGP | vizinhos (`display bgp peer`), habilitar processo, criar/remover peer, anunciar redes |
  | OSPF | vizinhanças (`display ospf peer brief`), habilitar processo, redes por área |
  | MPLS / LDP | sessões LDP, habilitar MPLS+LDP global e por interface |
  | Sistema | versão, CPU, memória, uptime, renomear (`sysname`), **salvar configuração** |
  | Novo Terminal | console CLI integrado usando a mesma sessão SSH |

- **Pré-visualização de CLI**: toda ação de configuração abre uma janela
  mostrando exatamente os comandos que serão executados (com `system-view`
  e `return`), com opção de copiar ou cancelar — nada é enviado sem o seu OK.
- **Tudo assíncrono**: as consultas SSH rodam em threads; a janela nunca
  congela.

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
│   └── profiles.py          # equipamentos salvos (~/.huaweibox)
└── ui/
    ├── login.py             # tela de conexão estilo WinBox
    ├── mainwindow.py        # menu lateral + área MDI
    ├── command_preview.py   # pré-visualização do CLI antes do envio
    ├── forms.py             # formulários genéricos
    ├── worker.py            # execução SSH fora da thread da UI
    ├── style.py             # tema visual (QSS)
    └── panels/              # Interfaces, VLANs, Rotas, BGP, OSPF, MPLS...
```

## Avisos

- Testado contra a sintaxe do VRP clássico (séries AR, NE, S). Versões
  diferentes podem variar em alguns `display`; os parsers ignoram linhas
  desconhecidas em vez de quebrar.
- A opção "Lembrar senha" grava a senha apenas ofuscada (base64) no disco;
  em ambientes sensíveis, deixe desmarcada.
- Configurações aplicadas **não são gravadas na flash automaticamente** —
  use *Salvar configuração* no módulo Sistema.
