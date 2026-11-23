# Histórico de versões — Cine Prime

## 0.1.0 — primeira versão pública (release candidate)

Esta versão reúne a primeira implementação funcional do Cine Prime para Linux.

### Funcionalidades
- Biblioteca de filmes e séries com pesquisa, favoritos e retoma de reprodução.
- Scanner de pastas e classificação automática de filmes e episódios.
- Capas online através de OMDb e TVmaze.
- Reprodução integrada com GStreamer `playbin3` em GTK4/Wayland.
- Controlos de reprodução, volume, velocidade, seek e selecção de faixas.
- Legendas incorporadas e externas, incluindo ajuste de sincronização.
- Backup e restauro da base de dados, configuração e capas geridas.
- Ferramentas de integridade, duplicados e manutenção da biblioteca.

### Validação técnica
- 239 testes descobertos na suite normal, sem falhas (80 ignorados por dependerem de ambiente GUI/integração).
- 79 testes GUI aprovados.
- 1 teste de integração GStreamer com erro fatal real aprovado.
- Pacote Debian/Ubuntu `cineprime_0.1.0_all.deb` construído e validado.

### Limitações conhecidas antes da distribuição alargada
- Ainda falta validação audiovisual subjectiva em diferentes equipamentos e formatos.
- Acessibilidade ainda precisa de verificação manual com Orca.
- Instalação/actualização/remoção APT real deve ser testada em ambiente limpo.
- O suporte de aceleração por hardware depende dos drivers e plugins instalados no sistema.
- A disponibilidade legal do nome Cine Prime não foi formalmente confirmada.
- A licença de distribuição deve ser definida pelo autor antes da publicação pública do código.