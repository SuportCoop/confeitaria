import os
import django
from decimal import Decimal

# Configura o ambiente Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'core.settings')
django.setup()

from django.contrib.auth import get_user_model
from caderninho.models import Cliente, Produto, ConfiguracaoGeral, Pedido, ItemPedido
from django.utils import timezone


def populate():
    print("Iniciando a carga de dados iniciais...")

    # 1. Configuração Geral (Singleton)
    config = ConfiguracaoGeral.get_solo()
    print(f"Configuração geral carregada: Chave PIX = {config.chave_pix}")

    # 2. Criar Superusuário (se não existir)
    User = get_user_model()
    username = 'admin'
    email = 'admin@lojinha.com'
    password = 'adminpassword123'

    if not User.objects.filter(username=username).exists():
        User.objects.create_superuser(username=username, email=email, password=password)
        print(f"Superusuário criado com sucesso!")
        print(f"-> Usuário: {username}")
        print(f"-> Senha: {password}")
    else:
        print("Superusuário 'admin' já existe.")

    # 3. Criar Clientes de Teste
    clientes_data = [
        {"nome": "Ana Silva", "whatsapp": "+5511999991111", "endereco": "Rua das Flores, 123 - Apto 4"},
        {"nome": "Bruno Costa", "whatsapp": "+5511999992222", "endereco": "Av. Paulista, 1000"},
        {"nome": "Carla Souza", "whatsapp": "+5511999993333", "endereco": "Rua dos Pinheiros, 456"},
    ]

    for c_info in clientes_data:
        cliente, created = Cliente.objects.get_or_create(
            nome=c_info["nome"],
            defaults={
                "whatsapp": c_info["whatsapp"], 
                "endereco": c_info["endereco"],
                "senha": "senha123"  # Senha padrão de teste
            }
        )
        if created:
            print(f"Cliente criado: {cliente.nome}")

    # 4. Criar Produtos de Teste
    produtos_data = [
        {
            "nome": "Bolo de Cenoura com Chocolate",
            "categoria": "BOLO",
            "preco": Decimal("35.00"),
            "descricao": "Bolo caseiro fofinho com cobertura generosa de brigadeiro.",
            "disponivel_hoje": True,
            "limite_diario": 5
        },
        {
            "nome": "Cento de Coxinha de Frango",
            "categoria": "SALGADO",
            "preco": Decimal("80.00"),
            "descricao": "Coxinhas fritas na hora, massa leve e muito recheio.",
            "disponivel_hoje": True,
            "limite_diario": 10
        },
        {
            "nome": "Brigadeiro Gourmet (Unidade)",
            "categoria": "DOCE",
            "preco": Decimal("4.50"),
            "descricao": "Feito com chocolate belga e granulado de alta qualidade.",
            "disponivel_hoje": True,
            "limite_diario": 150
        },
        {
            "nome": "Bolo de Prestígio Festivo",
            "categoria": "BOLO",
            "preco": Decimal("65.00"),
            "descricao": "Bolo de chocolate recheado com beijinho cremoso.",
            "disponivel_hoje": False,
            "limite_diario": 2
        },
        {
            "nome": "Empada de Palmito (Unidade)",
            "categoria": "SALGADO",
            "preco": Decimal("6.00"),
            "descricao": "Massa podre derrete na boca com recheio super cremoso.",
            "disponivel_hoje": True,
            "limite_diario": 50
        },
    ]

    for p_info in produtos_data:
        produto, created = Produto.objects.get_or_create(
            nome=p_info["nome"],
            defaults={
                "categoria": p_info["categoria"],
                "preco": p_info["preco"],
                "descricao": p_info["descricao"],
                "disponivel_hoje": p_info["disponivel_hoje"],
                "limite_diario": p_info["limite_diario"]
            }
        )
        if created:
            print(f"Produto criado: {produto.nome}")

    # 5. Criar alguns Pedidos de exemplo em Aberto para teste de fechamento
    cliente_ana = Cliente.objects.get(nome="Ana Silva")
    cliente_bruno = Cliente.objects.get(nome="Bruno Costa")
    
    prod_bolo = Produto.objects.get(nome="Bolo de Cenoura com Chocolate")
    prod_coxinha = Produto.objects.get(nome="Cento de Coxinha de Frango")
    prod_brigadeiro = Produto.objects.get(nome="Brigadeiro Gourmet (Unidade)")

    # Pedido 1 (Ana)
    if not Pedido.objects.filter(cliente=cliente_ana).exists():
        p1 = Pedido.objects.create(
            cliente=cliente_ana,
            taxa_entrega=Decimal("5.00"),
            observacoes="Entregar após as 14h.",
            status="PENDENTE",
            status_financeiro="ABERTO"
        )
        ItemPedido.objects.create(pedido=p1, produto=prod_bolo, quantidade=1, preco_unitario=prod_bolo.preco)
        ItemPedido.objects.create(pedido=p1, produto=prod_brigadeiro, quantidade=10, preco_unitario=prod_brigadeiro.preco)
        print(f"Pedido de teste criado para {cliente_ana.nome}")

    # Pedido 2 (Bruno)
    if not Pedido.objects.filter(cliente=cliente_bruno).exists():
        p2 = Pedido.objects.create(
            cliente=cliente_bruno,
            taxa_entrega=Decimal("0.00"),
            observacoes="Retirada no balcão.",
            status="ENTREGUE",
            status_financeiro="ABERTO"
        )
        ItemPedido.objects.create(pedido=p2, produto=prod_coxinha, quantidade=1, preco_unitario=prod_coxinha.preco)
        print(f"Pedido de teste criado para {cliente_bruno.nome}")

    print("Carga de dados finalizada com sucesso!")


if __name__ == '__main__':
    populate()
