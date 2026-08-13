"""
Camada de apresentação: janelas, abas e widgets.

As Views recebem um ViewModel pelo construtor e se limitam a conectar sinais a
widgets. Não consultam repositórios nem conhecem SQL — na versão antiga, três
das quatro abas chamavam `lap_storage` diretamente.
"""
