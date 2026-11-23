# Histórico de versões — Cine Prime

## 0.1.0-rc1 — primeira versão pública para validação

Primeira versão distribuível do Cine Prime para Linux, publicada como pré-lançamento enquanto decorrem validações manuais em diferentes equipamentos.

### Funcionalidades
- Biblioteca de filmes e séries com pesquisa, favoritos e retoma de reprodução.
- Scanner de pastas e classificação automática de filmes e episódios.
- Capas online através de OMDb e TVmaze.
- Reprodução integrada com GStreamer `playbin3` em GTK4/Wayland.
- Controlos de reprodução, volume, velocidade, seek e selecção de faixas.
- Legendas incorporadas e externas, incluindo ajuste de sincronização.
- Backup e restauro da base de dados, configuração e capas geridas.
- Ferramentas de integridade, duplicados e manutenção da biblioteca.

### Validação automatizada
- 239 testes descobertos na suite geral; 159 executados e aprovados, 80 ignorados por dependerem de ambiente GUI/integração.
- 79 testes GUI aprovados no CI.
- Teste de integração com erro fatal real do bus GStreamer aprovado no CI.
- Validação do lançador desktop e construção do pacote DEB aprovadas no CI.
- O pacote DEB é gerado automaticamente pelo GitHub Actions e disponibilizado como artefacto da execução.

### Validação manual ainda necessária
- Verificação audiovisual subjectiva (imagem, sincronização A/V, canais e volume) em diferentes equipamentos e formatos.
- Verificação de acessibilidade com Orca.
- Instalação, actualização e remoção APT em ambiente limpo.
- Testes adicionais de aceleração por hardware; o comportamento depende da GPU, dos drivers e dos plugins GStreamer disponíveis.
- Confirmação da disponibilidade legal do nome Cine Prime antes de uma divulgação comercial.

Esta versão é adequada para testes públicos e recolha de contribuições, mas não deve ser apresentada como certificada para todos os equipamentos.
