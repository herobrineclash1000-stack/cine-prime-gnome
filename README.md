# Cine Prime

Biblioteca pessoal de filmes e séries para Linux, com interface GTK4/libadwaita,
varredura automática de pastas, capas online, favoritos e retomada de onde você
parou.

## Dependências

Ubuntu 26.04 ou distribuição com GTK4 e GStreamer 1.24+:

```bash
sudo apt install python3-gi gir1.2-adw-1 gir1.2-gtk-4.0 \
  gstreamer1.0-tools gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-gtk4 ffmpeg
```

O plugin `gtk4paintablesink` é fornecido por `gstreamer1.0-gtk4` e permite
apresentar os fotogramas num widget GTK4 sem criar uma janela de vídeo externa.
Os pacotes `gstreamer1.0-plugins-bad` e `gstreamer1.0-plugins-extra` são
opcionais e acrescentam parsers, formatos e descodificadores adicionais; não
são dependências obrigatórias do Cine Prime.

### Aceleração por hardware (opcional)

Quando o sistema disponibiliza um descodificador VA-API compatível, o GStreamer
selecciona-o automaticamente através de `playbin3`. Sem driver/plugin compatível,
a reprodução continua a poder usar descodificação por software. A aceleração
depende da GPU, do driver e da versão dos plugins da distribuição; por isso,
nenhum driver Intel/AMD específico é imposto pelo pacote Cine Prime.

No Ubuntu 26.04, a combinação testada nesta máquina Intel foi
`gstreamer1.0-plugins-extra`, `intel-media-va-driver` e `vainfo` (com a dependência
`libigdgmm12`). Para diagnosticar o suporte no teu sistema, usa `vainfo` e
`gst-inspect-1.0 va`. Confirma sempre os nomes/versões disponíveis na tua
versão do Ubuntu antes de instalar drivers; não mistures o plugin antigo
`gstreamer1.0-vaapi` com uma stack GStreamer mais recente sem verificar a
compatibilidade.

## Construir e instalar o pacote Ubuntu

Para gerar um pacote DEB local (sem instalar dependências de desenvolvimento):

```bash
./packaging/build-deb.sh
```

O pacote fica em `dist/cineprime_0.1.0_all.deb`. Para o instalar e deixar o APT
resolver as dependências declaradas no pacote:

```bash
sudo apt install ./dist/cineprime_0.1.0_all.deb
```

O pacote instala o comando `cineprime`, o lançador do ambiente GNOME, o ícone e
os metadados AppStream. A configuração e a base de dados continuam em
`~/.config/linuxtv` e `~/.local/share/linuxtv` para preservar bibliotecas e
progresso de instalações anteriores; não renomear estas pastas manualmente.

## Como rodar

Interface gráfica:

```bash
python3 app.py
```

Ferramentas de linha de comandos para gerir a biblioteca:

```bash
python3 main.py scan ~/Videos [--kind auto|movie|series]
python3 main.py list [--kind movie|series]
python3 main.py rename 3 "John Wick 1"
python3 main.py cover 3 ~/Imagens/johnwick.jpg
```

## Configuração

- **Pastas da biblioteca**: `~/.config/linuxtv/config.json`
  (`kind`: `movie`, `series` ou `auto`)
- **Banco de dados**: `~/.local/share/linuxtv/linuxtv.db`
- **Chave da OMDb** (só necessária para filmes): variável de ambiente `OMDB_API_KEY`
  ou campo no arquivo de configuração

O Cine Prime continua a utilizar os dados existentes em `~/.config/linuxtv` e
`~/.local/share/linuxtv`, sem migração automática. Estes caminhos são mantidos
por compatibilidade com a biblioteca já existente.

## Backup e restauro

Na barra superior, abre o botão de definições e escolhe **Integridade e cópias
de segurança → Backup e restauro** para criar ou restaurar um ficheiro com
extensão `.cineprime`. Backups antigos com extensões `.cineflow` e `.gnometv` continuam
a ser aceites para restauro. Guarda os backups numa unidade diferente da unidade do
sistema (preferencialmente externa ou remota). O ficheiro contém a configuração,
incluindo a chave OMDb; por isso, mantém-no privado. A restauração exige
confirmação e substitui a base de dados/configuração actuais, mas não altera
os vídeos.

## Funcionalidades

- **Scan** com impressão digital (tamanho + hash) dos arquivos: detecta renomeação
  e movimentação, mantendo nome editado, capa e progresso
- **Classificação**: por pasta (`Filmes/`, `Series/`) ou automática (`S01E01`,
  `1x02`, `EP02`, …)
- **Capas**: OMDb (filmes) e TVmaze (séries); busca online com escolha do cartão
- **Favoritos**, **busca** e **retomada de progresso**
- **Reprodução integrada com GStreamer playbin3**: vídeo desenhado numa superfície
  GTK4/Wayland; controlos, volume, velocidade, seek e seleção de faixas são da aplicação
- **Legendas**: faixas incorporadas, ficheiros externos e ajuste de sincronização
- **Backup/restauro**: cópia consistente da base de dados, configuração e capas
  geridas pela aplicação; os vídeos originais nunca são incluídos. A restauração
  valida hashes e integridade SQLite antes de substituir os dados.
- **Manutenção centralizada**: definições agrupam scan, capas em falta, duplicados,
  diagnóstico profundo e backup/restauro num único diálogo categorizado.

## Testes

```bash
python3 -m unittest discover -s tests
```

Testes da GUI ficam desligados por padrão (precisam de ambiente gráfico):

```bash
CINEPRIME_GUI_TESTS=1 python3 -m unittest discover -s tests -p 'test_gui.py' -v
```

Teste de integração com um erro fatal real do bus GStreamer (precisa de GTK,
GStreamer, FFmpeg e uma sessão gráfica activa):

```bash
CINEPRIME_GST_INTEGRATION=1 python3 -m unittest discover -s tests -p 'test_gstreamer_integration.py' -v
```

O GitHub Actions executa os três níveis de testes, valida o lançador e constrói
o pacote DEB em cada push para `main`, pull request ou tag de versão. Quando
uma tag `v*.*.*` passa em todos os testes, o workflow publica automaticamente
uma GitHub Release com o pacote `.deb`, o SHA-256 e as notas de `CHANGELOG.md`.

## Identidade visual

O ícone e o wordmark do Cine Prime usam uma paleta preta e prateada.
Os SVG originais estão em `data/icons/hicolor/scalable/apps/` e
`data/branding/cine-prime-wordmark.svg`; o pacote instala o ícone no tema
`hicolor` e o wordmark em `/usr/share/cineprime/branding/`.

## Contribuir

O Cine Prime é software livre, distribuído sob a licença GNU GPL versão 3 ou, à tua escolha, qualquer versão posterior (`GPL-3.0-or-later`). A comunidade pode ajudar com correções, testes, traduções, documentação, propostas de funcionalidades e código.

Antes de abrir uma proposta, consulta `CONTRIBUTING.md` e o nosso `CODE_OF_CONDUCT.md`. Usa as Issues para relatar erros ou discutir melhorias e Pull Requests para propor alterações. Não publiques bibliotecas multimédia, bases de dados, cópias de segurança, chaves API, tokens ou outros dados privados.

## Licença

Copyright (C) 2026 Cine Prime contributors.

Este programa é software livre: podes redistribuí-lo e/ou modificá-lo nos termos da GNU General Public License, publicada pela Free Software Foundation, versão 3 ou (à tua escolha) qualquer versão posterior. É distribuído na esperança de ser útil, mas SEM QUALQUER GARANTIA; consulta o ficheiro `LICENSE` para os termos completos.