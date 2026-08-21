from decimal import Decimal
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Cliente, Produto, Pedido, ItemPedido, FechamentoMensal, ConfiguracaoGeral
from .services import (
    crc16_ccitt,
    generate_static_pix_payload,
    criar_pedido,
    fechar_mes_cliente,
    marcar_fechamento_como_pago,
    obter_dados_dashboard,
    gerar_mensagem_whatsapp
)


class PixAndCrcTests(TestCase):
    """
    Testes relacionados ao cálculo de CRC16 e geração do payload PIX EMV.
    """
    def test_crc16_ccitt(self):
        # Teste clássico para o CRC16-CCITT com string conhecida
        test_string = "123456789"
        expected_crc = "29B1"  # Valor padrão esperado para CRC-CCITT (0xFFFF)
        self.assertEqual(crc16_ccitt(test_string), expected_crc)

    def test_generate_static_pix_payload(self):
        chave = "financeiro@lojinha.com"
        titular = "Confeitaria da Lojinha"
        cidade = "Sao Paulo"
        valor = Decimal("150.50")
        
        payload = generate_static_pix_payload(chave, titular, cidade, valor)
        
        # O payload deve começar com o indicador de formato EMV 000201
        self.assertTrue(payload.startswith("000201"))
        # Deve conter a chave pix
        self.assertIn(chave, payload)
        # Deve conter o valor formatado
        self.assertIn("150.50", payload)
        # Deve terminar com a tag de CRC 6304 + 4 caracteres de checksum hex
        self.assertTrue(len(payload.split("6304")[-1]) == 4)


class PedidoEstoqueTests(TestCase):
    """
    Testes de criação de pedido e limite de produção diária.
    """
    def setUp(self):
        self.cliente = Cliente.objects.create(
            nome="Maria Teste",
            whatsapp="+5511988887777",
            senha="password123"
        )
        self.produto_ilimitado = Produto.objects.create(
            nome="Empada de Frango",
            categoria="SALGADO",
            preco=Decimal("6.00"),
            disponivel_hoje=True,
            limite_diario=0
        )
        self.produto_limitado = Produto.objects.create(
            nome="Bolo Gourmet",
            categoria="BOLO",
            preco=Decimal("50.00"),
            disponivel_hoje=True,
            limite_diario=5
        )
        self.produto_indisponivel = Produto.objects.create(
            nome="Brigadeiro Diet",
            categoria="DOCE",
            preco=Decimal("4.00"),
            disponivel_hoje=False,
            limite_diario=10
        )

    def test_criar_pedido_sucesso(self):
        itens = [
            {'produto_id': self.produto_ilimitado.id, 'quantidade': 10},
            {'produto_id': self.produto_limitado.id, 'quantidade': 3}
        ]
        
        pedido = criar_pedido(self.cliente, itens, taxa_entrega=Decimal("5.00"))
        
        self.assertEqual(pedido.cliente, self.cliente)
        self.assertEqual(pedido.status, 'PENDENTE')
        self.assertEqual(pedido.status_financeiro, 'ABERTO')
        self.assertEqual(pedido.taxa_entrega, Decimal("5.00"))
        self.assertEqual(pedido.itens.count(), 2)
        
        # Preço congelado no item do pedido
        self.assertEqual(pedido.itens.get(produto=self.produto_limitado).preco_unitario, Decimal("50.00"))
        self.assertEqual(pedido.total_pedido, Decimal("215.00"))  # (10 * 6) + (3 * 50) + 5

    def test_criar_pedido_indisponivel(self):
        itens = [{'produto_id': self.produto_indisponivel.id, 'quantidade': 1}]
        with self.assertRaises(ValidationError):
            criar_pedido(self.cliente, itens)

    def test_criar_pedido_excede_limite_diario(self):
        # 1. Primeira compra de 3 unidades do produto limitado (limite total = 5)
        criar_pedido(self.cliente, [{'produto_id': self.produto_limitado.id, 'quantidade': 3}])
        
        # 2. Segunda compra de mais 3 unidades no mesmo dia (excederá o limite total de 5)
        with self.assertRaises(ValidationError) as context:
            criar_pedido(self.cliente, [{'produto_id': self.produto_limitado.id, 'quantidade': 3}])
        
        self.assertIn("Limite de produção diário excedido", str(context.exception))


class FechamentoContasTests(TestCase):
    """
    Testes para consolidação e fechamento de contas do caderninho.
    """
    def setUp(self):
        self.cliente = Cliente.objects.create(nome="João Silva", whatsapp="+5511977776666", senha="password123")
        self.produto = Produto.objects.create(
            nome="Doce de Colher", categoria="DOCE", preco=Decimal("10.00"), disponivel_hoje=True
        )
        
        # Configurações iniciais
        ConfiguracaoGeral.get_solo()

    def test_fechamento_mensal_fluxo_completo(self):
        hoje = timezone.localtime(timezone.now())
        mes = hoje.month
        ano = hoje.year

        # 1. Cria dois pedidos em aberto no mês atual
        p1 = criar_pedido(self.cliente, [{'produto_id': self.produto.id, 'quantidade': 2}])
        p2 = criar_pedido(self.cliente, [{'produto_id': self.produto.id, 'quantidade': 3}], taxa_entrega=Decimal("7.00"))

        # 2. Realiza o fechamento
        fechamento = fechar_mes_cliente(self.cliente, ano, mes)

        self.assertEqual(fechamento.cliente, self.cliente)
        self.assertEqual(fechamento.ano, ano)
        self.assertEqual(fechamento.mes, mes)
        # Total devedor: p1 (2 * 10 = 20) + p2 (3 * 10 + 7 = 37) = 57.00
        self.assertEqual(fechamento.total_devedor, Decimal("57.00"))
        self.assertFalse(fechamento.pago)

        # 3. Verifica se os pedidos foram associados ao fechamento
        p1.refresh_from_db()
        p2.refresh_from_db()
        self.assertEqual(p1.fechamento_mensal, fechamento)
        self.assertEqual(p2.fechamento_mensal, fechamento)

        # 4. Tenta fechar novamente sem novos pedidos (deve falhar)
        with self.assertRaises(ValidationError):
            fechar_mes_cliente(self.cliente, ano, mes)

        # 5. Liquida o fechamento
        marcar_fechamento_como_pago(fechamento)
        
        fechamento.refresh_from_db()
        p1.refresh_from_db()
        p2.refresh_from_db()
        
        self.assertTrue(fechamento.pago)
        self.assertEqual(p1.status_financeiro, 'PAGO')
        self.assertEqual(p2.status_financeiro, 'PAGO')

    def test_geracao_url_whatsapp(self):
        hoje = timezone.localtime(timezone.now())
        p = criar_pedido(self.cliente, [{'produto_id': self.produto.id, 'quantidade': 1}])
        fechamento = fechar_mes_cliente(self.cliente, hoje.year, hoje.month)
        
        url = gerar_mensagem_whatsapp(fechamento)
        
        # Deve ser uma URL no padrão wa.me
        self.assertTrue(url.startswith("https://wa.me/"))
        # Deve conter o número higienizado (sem caracteres especiais)
        self.assertIn("5511977776666", url)
        # Deve conter caracteres url encoded (%20, %0A, etc)
        self.assertIn("%", url)


class DashboardStatsTests(TestCase):
    """
    Testes agregados do Dashboard.
    """
    def test_obter_dados_dashboard(self):
        cliente = Cliente.objects.create(nome="Ana Maria", whatsapp="+5511955554444", senha="password123")
        produto = Produto.objects.create(
            nome="Bolo Simples", categoria="BOLO", preco=Decimal("20.00"), disponivel_hoje=True
        )
        
        # Pedido Pago
        p_pago = criar_pedido(cliente, [{'produto_id': produto.id, 'quantidade': 1}])
        p_pago.status_financeiro = 'PAGO'
        p_pago.save()
        
        # Pedido Aberto
        criar_pedido(cliente, [{'produto_id': produto.id, 'quantidade': 2}], taxa_entrega=Decimal("5.00"))

        hoje = timezone.localtime(timezone.now())
        dados = obter_dados_dashboard(hoje.month, hoje.year)

        self.assertEqual(dados['total_pedidos'], 2)
        # Pago: 1 * 20 = 20.00
        self.assertEqual(dados['faturamento_pago'], Decimal("20.00"))
        # Aberto: 2 * 20 + 5 = 45.00
        self.assertEqual(dados['faturamento_receber'], Decimal("45.00"))
        # Total: 65.00
        self.assertEqual(dados['faturamento_total'], Decimal("65.00"))
        # Item mais vendido
        self.assertEqual(dados['itens_vendidos'][0]['produto__nome'], "Bolo Simples")
        self.assertEqual(dados['itens_vendidos'][0]['total_quantidade'], 3)


class ViewAuthorizationTests(TestCase):
    """
    Testes de autorização de acesso às views.
    """
    def setUp(self):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        self.staff_user = User.objects.create_user(
            username='staff_test',
            password='password123',
            is_staff=True
        )
        self.regular_user = User.objects.create_user(
            username='regular_test',
            password='password123',
            is_staff=False
        )
        self.cliente = Cliente.objects.create(
            nome="Cliente Historico Teste",
            whatsapp="+5511911112222",
            senha="password123"
        )

    def test_admin_dashboard_redirects_non_staff(self):
        # Usuário anônimo
        response = self.client.get('/painel/')
        self.assertRedirects(response, '/painel/login/?next=/painel/')
        
        # Usuário regular (não staff)
        self.client.login(username='regular_test', password='password123')
        response = self.client.get('/painel/')
        self.assertRedirects(response, '/painel/login/?next=/painel/')

    def test_admin_dashboard_allows_staff(self):
        self.client.login(username='staff_test', password='password123')
        response = self.client.get('/painel/')
        self.assertEqual(response.status_code, 200)

    def test_cliente_historico_requires_session(self):
        # Sem cliente na sessão, redireciona para a identificação
        response = self.client.get('/meus-pedidos/')
        self.assertRedirects(response, '/')

    def test_cliente_historico_with_session(self):
        # Simula cliente na sessão
        session = self.client.session
        session['cliente_id'] = self.cliente.id
        session.save()
        
        response = self.client.get('/meus-pedidos/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Cliente Historico Teste")

    def test_identificacao_cliente_post_correct_password(self):
        response = self.client.post('/', {
            'cliente_existente': self.cliente.id,
            'senha_login': 'password123'
        })
        self.assertRedirects(response, '/cardapio/')
        self.assertEqual(self.client.session['cliente_id'], self.cliente.id)

    def test_identificacao_cliente_post_incorrect_password(self):
        response = self.client.post('/', {
            'cliente_existente': self.cliente.id,
            'senha_login': 'wrong_password'
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse('cliente_id' in self.client.session)

    def test_admin_cliente_editar_redirects_non_staff(self):
        response = self.client.get(f'/painel/clientes/editar/{self.cliente.id}/')
        self.assertRedirects(response, f'/painel/login/?next=/painel/clientes/editar/{self.cliente.id}/')

    def test_admin_cliente_editar_allows_staff_and_updates_password(self):
        self.client.login(username='staff_test', password='password123')
        
        # Teste GET
        response = self.client.get(f'/painel/clientes/editar/{self.cliente.id}/')
        self.assertEqual(response.status_code, 200)

        # Teste POST para atualizar a senha
        response = self.client.post(f'/painel/clientes/editar/{self.cliente.id}/', {
            'nome': 'Cliente Historico Teste Editado',
            'whatsapp': '+5511911112222',
            'senha': 'newpassword123',
            'ativo': True
        })
        self.assertRedirects(response, f'/painel/clientes/{self.cliente.id}/')
        
        # Verifica se atualizou no banco de dados e se a senha nova funciona
        self.cliente.refresh_from_db()
        self.assertEqual(self.cliente.nome, 'Cliente Historico Teste Editado')
        
        from django.contrib.auth.hashers import check_password
        self.assertTrue(check_password('newpassword123', self.cliente.senha))

    def test_admin_cliente_reset_senha_redirects_non_staff(self):
        response = self.client.post(f'/painel/clientes/{self.cliente.id}/reset-senha/', {
            'nova_senha': 'anothernewpassword'
        })
        self.assertRedirects(response, f'/painel/login/?next=/painel/clientes/{self.cliente.id}/reset-senha/')

    def test_admin_cliente_reset_senha_allows_staff_and_updates_password(self):
        self.client.login(username='staff_test', password='password123')
        
        response = self.client.post(f'/painel/clientes/{self.cliente.id}/reset-senha/', {
            'nova_senha': 'anothernewpassword'
        })
        self.assertRedirects(response, f'/painel/clientes/{self.cliente.id}/')
        
        # Verifica se a senha foi atualizada
        self.cliente.refresh_from_db()
        from django.contrib.auth.hashers import check_password
        self.assertTrue(check_password('anothernewpassword', self.cliente.senha))

