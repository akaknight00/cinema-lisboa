# Sessões de cinema em Lisboa: recolha automática

Todos os dias, o GitHub corre `scraper/scrape.py`, que lê as fontes em `scraper/sources.json`
e escreve `docs/sessions.json`. Se uma fonte falhar, as sessões antigas dessa fonte mantêm-se.

## Pôr a funcionar
1. Cria uma conta em github.com e um repositório novo (público, para poderes usar o GitHub Pages depois).
2. Carrega para lá o conteúdo desta pasta (botão *Add file → Upload files*; mantém as pastas, incluindo `.github`).
3. Separador **Actions** → *Atualizar sessões* → **Run workflow**.
4. Abre a execução e vê o relatório: cada fonte diz `OK` (com o nº de sessões) ou `FALHA` (com o motivo).
5. Se correu, aparece `docs/sessions.json` no repositório.

## Testar no teu computador (opcional)
    pip install -r scraper/requirements.txt
    python scraper/scrape.py --debug

## Acrescentar um espaço
Junta uma linha a `scraper/sources.json`. Se o site não tiver dados estruturados (JSON-LD),
é preciso um parser próprio em `scrape.py`: cola-me o HTML guardado em `scraper/debug/`
e eu escrevo-o.

## Nota
Os pedidos são feitos uma vez por dia. Confirma os termos de utilização de cada site
antes de acrescentares fontes.
