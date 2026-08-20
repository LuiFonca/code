"""
Gravar a configuração de volta no `.env`.

Uma tela de configuração que não persiste é pior que nenhuma: o usuário digita o
IP, conecta, fecha o programa e no dia seguinte está tudo em branco de novo — só
que agora ele acredita que configurou. Este módulo é a metade que faltava do
`_load_dotenv`.

**A decisão que organiza o resto: o arquivo é do usuário, não nosso.** O
`.env.example` tem 98 linhas, quase todas comentário explicando *por que* cada
opção existe — o IP sem padrão, o custo real da API, o INTENT do Discord que
ninguém lembra de habilitar. Reescrever o arquivo a partir dos valores atuais
seria trivial e destruiria tudo isso na primeira vez que alguém clicasse em
Salvar. Então a edição é cirúrgica: mexe na linha da chave e não encosta em mais
nada.

Três comportamentos que vêm disso, e que os testes fixam:

1. **Chave existente muda de valor no lugar**, mantendo a posição. Quem abrir o
   arquivo depois encontra o valor junto do comentário que o explica.
2. **Chave comentada é descomentada no lugar.** O `.env.example` distribui
   opções como `# GT7_VOICE_NAME=Luciana`, prontas para usar. Ligar a voz pela
   interface preenche exatamente aquela linha, em vez de deixar a versão morta
   em cima e a viva no rodapé.
3. **Chave nova vai para o fim**, sob um cabeçalho que diz de onde veio.

O que este módulo deliberadamente **não** faz: interpretar aspas, expandir
variáveis ou preservar comentário na mesma linha do valor. O leitor
(`_load_dotenv`) também não faz nada disso, e um escritor mais esperto que o
leitor produziria arquivos que o programa não consegue reler.
"""

from __future__ import annotations

import os
from pathlib import Path

from .settings import ENV_PREFIX

HEADER = "# ---------- ajustado pela interface ----------"


def _assignment(key: str, value: str) -> str:
    return f"{key}={value}"


def _is_assignment_for(line: str, key: str) -> bool:
    """A linha atribui `key`, esteja ela ativa ou comentada?

    Aceita `GT7_PS_IP=x`, `  GT7_PS_IP = x` e `# GT7_PS_IP=x`, porque as três
    formas aparecem no `.env.example` e todas representam a mesma intenção do
    ponto de vista de quem está editando pela tela.
    """
    stripped = line.strip()
    if stripped.startswith("#"):
        stripped = stripped.lstrip("#").strip()
    if "=" not in stripped:
        return False
    return stripped.partition("=")[0].strip() == key


def update_env_text(text: str, changes: dict[str, str]) -> str:
    """Aplica `changes` ao conteúdo de um `.env`, preservando o resto.

    Função pura: recebe o texto, devolve o texto. É o que permite verificar a
    preservação de comentários sem escrever em disco nenhum, e é o núcleo do
    módulo — `save_env` só cuida de I/O em volta dela.
    """
    if not changes:
        return text

    pending = dict(changes)
    lines = text.splitlines()
    output: list[str] = []

    for line in lines:
        for key in list(pending):
            if _is_assignment_for(line, key):
                output.append(_assignment(key, pending.pop(key)))
                break
        else:
            output.append(line)

    if pending:
        # Só abre a seção nova se ela não existir: salvar duas vezes não pode
        # empilhar cabeçalhos.
        if output and output[-1].strip():
            output.append("")
        if HEADER not in output:
            output.append(HEADER)
        for key, value in pending.items():
            output.append(_assignment(key, value))

    result = "\n".join(output)
    # Arquivo de configuração termina em newline. Sem isto, salvar duas vezes
    # gruda a segunda seção na última linha da primeira.
    return result + "\n" if result else ""


def save_env(path: Path, changes: dict[str, str]) -> None:
    """Grava `changes` no `.env` em `path`, criando-o se não existir."""
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(update_env_text(existing, changes), encoding="utf-8")


def overridden_by_environment(keys: list[str] | tuple[str, ...]) -> list[str]:
    """Quais dessas chaves estão definidas no ambiente, vencendo o arquivo.

    A precedência documentada é `os.environ` > `.env` > padrão, e ela está
    certa — quem exporta uma variável na mão espera que ela mande. Mas isso cria
    o pior modo de falha possível para uma tela de configuração: o usuário
    digita o IP, salva, o arquivo é gravado corretamente, e **nada acontece**,
    porque um `export GT7_PS_IP` antigo continua vencendo. Ele então conclui que
    o programa está quebrado.

    A tela usa isto para avisar em vez de fingir que salvou.
    """
    return [key for key in keys if os.environ.get(key)]


def env_key_for(field_name: str) -> str:
    """Nome da variável de ambiente de um campo de configuração."""
    return f"{ENV_PREFIX}{field_name.upper()}"
