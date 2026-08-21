from decimal import Decimal
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import transaction
from django_ratelimit.decorators import ratelimit
from django.db.models import Sum, Q, F
from django.contrib.auth.hashers import check_password

def staff_member_required(view_func):
    decorator = user_passes_test(
        lambda u: u.is_active and u.is_staff,
        login_url='admin_login'
    )
    return decorator(view_func)


from .models import Cliente, Produto, Pedido, ItemPedido, FechamentoMensal, ConfiguracaoGeral
from .forms import ProdutoForm, ClienteForm
from .services import (
    criar_pedido,
    fechar_mes_cliente,
    marcar_fechamento_como_pago,
    gerar_mensagem_whatsapp,
    obter_dados_dashboard
)


# =====================================================================
# CLIENTE (MOBILE-FIRST & PREMIUM DESIGN)
# =====================================================================

@ratelimit(key='ip', rate='10/m', block=True)
def identificacao_cliente(request):
    """
    Identificação do cliente com senha de acesso para privacidade.
    """
    if 'cliente_id' in request.session:
        cliente = Cliente.objects.filter(id=request.session['cliente_id'], ativo=True).first()
        if cliente:
            request.session['cliente_name'] = cliente.nome
            return redirect('cardapio')

    if request.method == 'POST':
        cliente_existente_id = request.POST.get('cliente_existente')
        senha_login = request.POST.get('senha_login')
        
        novo_nome = request.POST.get('novo_nome')
        novo_whatsapp = request.POST.get('novo_whatsapp')
        nova_senha = request.POST.get('nova_senha')
        novo_endereco = request.POST.get('novo_endereco')

        if cliente_existente_id:
            cliente = get_object_or_404(Cliente, id=cliente_existente_id, ativo=True)
            if senha_login and check_password(senha_login, cliente.senha):
                request.session['cliente_id'] = cliente.id
                request.session['cliente_name'] = cliente.nome
                return redirect('cardapio')
            else:
                messages.error(request, "Senha incorreta para o cliente selecionado.")

        elif novo_nome and novo_whatsapp and nova_senha:
            try:
                with transaction.atomic():
                    cliente = Cliente.objects.create(
                        nome=novo_nome.strip(),
                        whatsapp=novo_whatsapp.strip(),
                        senha=nova_senha,
                        endereco=novo_endereco.strip() if novo_endereco else ""
                    )
                request.session['cliente_id'] = cliente.id
                request.session['cliente_name'] = cliente.nome
                messages.success(request, f"Olá, {cliente.nome}! Cadastro realizado com sucesso.")
                return redirect('cardapio')
            except Exception as e:
                messages.error(request, f"Erro ao realizar cadastro: {str(e)}")
        else:
            messages.error(request, "Preencha todos os campos obrigatórios (incluindo a senha).")

    clientes = Cliente.objects.filter(ativo=True).order_by('nome')
    config = ConfiguracaoGeral.get_solo()
    import re
    whatsapp_loja_limpo = re.sub(r'\D', '', config.whatsapp_loja)

    return render(request, 'caderninho/identificacao.html', {
        'clientes': clientes,
        'whatsapp_loja': whatsapp_loja_limpo
    })


@ratelimit(key='ip', rate='5/m', block=True)
def cardapio(request):
    """
    Cardápio interativo e vitrine premium de vendas diárias.
    """
    cliente_id = request.session.get('cliente_id')
    if not cliente_id:
        return redirect('identificacao_cliente')

    cliente = get_object_or_404(Cliente, id=cliente_id, ativo=True)
    request.session['cliente_name'] = cliente.nome
    produtos = Produto.objects.filter(disponivel_hoje=True)

    if request.method == 'POST':
        itens_data = []
        observacoes = request.POST.get('observacoes', '').strip()
        
        # Taxa de entrega removida (apenas retirada no local)
        taxa_entrega = Decimal("0.00")

        for produto in produtos:
            qty_input = request.POST.get(f'quantidade_{produto.id}', '0')
            try:
                qty = int(qty_input)
                if qty > 0:
                    itens_data.append({
                        'produto_id': produto.id,
                        'quantidade': qty
                    })
            except ValueError:
                continue

        if not itens_data:
            messages.error(request, "Selecione pelo menos 1 item para enviar o pedido.")
        else:
            try:
                pedido = criar_pedido(
                    cliente=cliente,
                    itens_data=itens_data,
                    taxa_entrega=taxa_entrega,
                    observacoes=observacoes
                )

                messages.success(request, "Pedido realizado com sucesso!")
                return redirect('pedido_sucesso', pedido_id=pedido.id)

            except ValidationError as e:
                messages.error(request, f"Erro: {e.message}")
            except Exception as e:
                messages.error(request, f"Erro ao registrar pedido: {str(e)}")

    return render(request, 'caderninho/cardapio.html', {
        'cliente': cliente,
        'produtos': produtos
    })


def pedido_sucesso(request, pedido_id):
    """
    Tela de confirmação do pedido feito pelo cliente.
    """
    pedido = get_object_or_404(Pedido, id=pedido_id)
    return render(request, 'caderninho/sucesso.html', {'pedido': pedido})


def deslogar_cliente(request):
    """
    Desloga o cliente da sessão ativa.
    """
    if 'cliente_id' in request.session:
        del request.session['cliente_id']
    if 'cliente_name' in request.session:
        del request.session['cliente_name']
    return redirect('identificacao_cliente')


def cliente_historico(request):
    """
    Portal do Cliente para visualização de histórico de pedidos e faturas pendentes.
    """
    cliente_id = request.session.get('cliente_id')
    if not cliente_id:
        return redirect('identificacao_cliente')

    cliente = get_object_or_404(Cliente, id=cliente_id, ativo=True)
    request.session['cliente_name'] = cliente.nome
    
    # Todos os pedidos do cliente
    pedidos = Pedido.objects.filter(cliente=cliente).order_by('-data_criacao')
    
    # Faturamentos fechados
    fechamentos = FechamentoMensal.objects.filter(cliente=cliente).order_by('-ano', '-mes')
    
    # Saldo devedor total acumulado aberto (pedidos em aberto que ainda não foram consolidados)
    pedidos_abertos_nfechados = Pedido.objects.filter(
        cliente=cliente,
        status_financeiro='ABERTO',
        fechamento_mensal__isnull=True
    ).exclude(status='CANCELADO')
    
    saldo_aberto_atual = Decimal("0.00")
    for p in pedidos_abertos_nfechados:
        saldo_aberto_atual += p.total_pedido

    # Faturas consolidadas não pagas
    faturas_abertas = fechamentos.filter(pago=False)
    saldo_faturas_pendentes = faturas_abertas.aggregate(total=Sum('total_devedor'))['total'] or Decimal("0.00")

    saldo_total_devedor = saldo_aberto_atual + saldo_faturas_pendentes

    # Obter dados do PIX
    config = ConfiguracaoGeral.get_solo()
    pix_copia_cola = ""
    if saldo_total_devedor > 0:
        from .services import generate_static_pix_payload
        pix_copia_cola = generate_static_pix_payload(
            chave_pix=config.chave_pix,
            nome_titular=config.nome_titular,
            cidade_titular=config.cidade_titular,
            valor=saldo_total_devedor
        )

    return render(request, 'caderninho/cliente_historico.html', {
        'cliente': cliente,
        'pedidos': pedidos,
        'fechamentos': fechamentos,
        'saldo_aberto_atual': saldo_aberto_atual,
        'saldo_faturas_pendentes': saldo_faturas_pendentes,
        'saldo_total_devedor': saldo_total_devedor,
        'config': config,
        'pix_copia_cola': pix_copia_cola
    })



# =====================================================================
# ADMIN PORTAL CUSTOMIZADO (PROTEGIDO POR STAFF)
# =====================================================================

@staff_member_required
def admin_dashboard(request):
    """
    Dashboard administrativo com relatórios financeiros detalhados.
    """
    hoje = timezone.localtime(timezone.now())
    mes_str = request.GET.get('mes', str(hoje.month))
    ano_str = request.GET.get('ano', str(hoje.year))

    try:
        mes = int(mes_str)
        ano = int(ano_str)
    except ValueError:
        mes = hoje.month
        ano = hoje.year

    dados = obter_dados_dashboard(mes=mes, ano=ano)
    
    meses = [
        (1, 'Janeiro'), (2, 'Fevereiro'), (3, 'Março'), (4, 'Abril'),
        (5, 'Maio'), (6, 'Junho'), (7, 'Julho'), (8, 'Agosto'),
        (9, 'Setembro'), (10, 'Outubro'), (11, 'Novembro'), (12, 'Dezembro')
    ]
    anos = range(hoje.year - 2, hoje.year + 2)

    context = {
        **dados,
        'meses_list': meses,
        'anos_list': anos,
        'mes_atual_filtro': mes,
        'ano_atual_filtro': ano,
    }
    return render(request, 'caderninho/admin_dashboard.html', context)


@staff_member_required
def admin_produtos(request):
    """
    Gestão de produtos no painel administrativo.
    """
    produtos = Produto.objects.all().order_by('nome')
    return render(request, 'caderninho/admin_produtos.html', {'produtos': produtos})


@staff_member_required
def admin_produto_toggle(request, produto_id):
    """
    Ativa/Desativa rapidamente a disponibilidade diária de um produto.
    """
    produto = get_object_or_404(Produto, id=produto_id)
    produto.disponivel_hoje = not produto.disponivel_hoje
    produto.save()
    messages.success(request, f"Disponibilidade do produto '{produto.nome}' atualizada.")
    return redirect('admin_produtos')


@staff_member_required
def admin_produto_criar(request):
    """
    Formulário para cadastrar novo produto.
    """
    if request.method == 'POST':
        form = ProdutoForm(request.POST, request.FILES)
        if form.is_valid():
            produto = form.save()
            messages.success(request, f"Produto '{produto.nome}' cadastrado com sucesso!")
            return redirect('admin_produtos')
    else:
        form = ProdutoForm()
    return render(request, 'caderninho/admin_produto_form.html', {'form': form, 'titulo': 'Cadastrar Produto'})


@staff_member_required
def admin_produto_editar(request, produto_id):
    """
    Formulário para editar produto existente.
    """
    produto = get_object_or_404(Produto, id=produto_id)
    if request.method == 'POST':
        form = ProdutoForm(request.POST, request.FILES, instance=produto)
        if form.is_valid():
            form.save()
            messages.success(request, f"Produto '{produto.nome}' editado com sucesso!")
            return redirect('admin_produtos')
    else:
        form = ProdutoForm(instance=produto)
    return render(request, 'caderninho/admin_produto_form.html', {'form': form, 'titulo': 'Editar Produto', 'produto': produto})


@staff_member_required
def admin_pedidos(request):
    """
    Filtro e listagem de todas as vendas e pedidos cadastrados no sistema.
    """
    status_filter = request.GET.get('status', '')
    financeiro_filter = request.GET.get('financeiro', '')
    busca = request.GET.get('busca', '')

    pedidos = Pedido.objects.all().prefetch_related('itens__produto', 'cliente')

    if status_filter:
        pedidos = pedidos.filter(status=status_filter)
    if financeiro_filter:
        pedidos = pedidos.filter(status_financeiro=financeiro_filter)
    if busca:
        pedidos = pedidos.filter(
            Q(cliente__nome__icontains=busca) |
            Q(id__icontains=busca) |
            Q(observacoes__icontains=busca)
        )

    pedidos = pedidos.order_by('-data_criacao')

    # Opções para filtros
    status_choices = Pedido.STATUS_CHOICES
    financeiro_choices = Pedido.STATUS_FINANCEIRO_CHOICES

    return render(request, 'caderninho/admin_pedidos.html', {
        'pedidos': pedidos,
        'status_choices': status_choices,
        'financeiro_choices': financeiro_choices,
        'status_filter': status_filter,
        'financeiro_filter': financeiro_filter,
        'busca': busca
    })


@staff_member_required
def admin_pedido_criar(request):
    """
    Lógica de cadastro manual de vendas realizadas no balcão ou solicitadas via áudio.
    """
    if request.method == 'POST':
        cliente_id = request.POST.get('cliente')
        observacoes = request.POST.get('observacoes', '').strip()
        
        # Taxa de entrega removida (apenas retirada)
        taxa_entrega = Decimal("0.00")

        cliente = get_object_or_404(Cliente, id=cliente_id, ativo=True)
        produtos = Produto.objects.filter(disponivel_hoje=True)
        
        itens_data = []
        for produto in produtos:
            qty_input = request.POST.get(f'quantidade_{produto.id}', '0')
            try:
                qty = int(qty_input)
                if qty > 0:
                    itens_data.append({
                        'produto_id': produto.id,
                        'quantidade': qty
                    })
            except ValueError:
                continue

        if not itens_data:
            messages.error(request, "Selecione pelo menos 1 item para realizar a venda.")
        else:
            try:
                pedido = criar_pedido(
                    cliente=cliente,
                    itens_data=itens_data,
                    taxa_entrega=taxa_entrega,
                    observacoes=observacoes
                )
                messages.success(request, f"Venda Manual #{pedido.id} registrada com sucesso!")
                return redirect('admin_pedidos')
            except ValidationError as e:
                messages.error(request, f"Falha na validação da venda: {e.message}")
            except Exception as e:
                messages.error(request, f"Erro inesperado: {str(e)}")

    clientes = Cliente.objects.filter(ativo=True).order_by('nome')
    produtos = Produto.objects.filter(disponivel_hoje=True).order_by('nome')
    return render(request, 'caderninho/admin_pedido_form.html', {
        'clientes': clientes,
        'produtos': produtos
    })


@staff_member_required
def admin_pedido_alterar_status(request, pedido_id, novo_status):
    """
    Muda rapidamente o status de preparação do pedido.
    """
    pedido = get_object_or_404(Pedido, id=pedido_id)
    if novo_status in dict(Pedido.STATUS_CHOICES):
        pedido.status = novo_status
        pedido.save()
        messages.success(request, f"Status do Pedido #{pedido.id} alterado para {pedido.get_status_display()}.")
    return redirect(request.META.get('HTTP_REFERER', 'admin_pedidos'))


@staff_member_required
def admin_pedido_alterar_financeiro(request, pedido_id, novo_financeiro):
    """
    Muda rapidamente o status financeiro do pedido.
    """
    pedido = get_object_or_404(Pedido, id=pedido_id)
    if novo_financeiro in dict(Pedido.STATUS_FINANCEIRO_CHOICES):
        pedido.status_financeiro = novo_financeiro
        pedido.save()
        messages.success(request, f"Status Financeiro do Pedido #{pedido.id} alterado para {pedido.get_status_financeiro_display()}.")
    return redirect(request.META.get('HTTP_REFERER', 'admin_pedidos'))


@staff_member_required
def admin_clientes(request):
    """
    Painel de controle de clientes e conta corrente (caderninho de cada um).
    """
    clientes = Cliente.objects.all().order_by('nome')
    
    clientes_list = []
    for cliente in clientes:
        # Calcular saldo aberto atual (pedidos não consolidados e não cancelados)
        pedidos_abertos = Pedido.objects.filter(
            cliente=cliente,
            status_financeiro='ABERTO',
            fechamento_mensal__isnull=True
        ).exclude(status='CANCELADO')
        
        saldo_aberto = Decimal("0.00")
        for p in pedidos_abertos:
            saldo_aberto += p.total_pedido

        # Calcular saldo de fechamentos abertos
        faturas_abertas = FechamentoMensal.objects.filter(cliente=cliente, pago=False)
        saldo_faturas = faturas_abertas.aggregate(total=Sum('total_devedor'))['total'] or Decimal("0.00")

        saldo_total = saldo_aberto + saldo_faturas

        clientes_list.append({
            'obj': cliente,
            'saldo_aberto': saldo_aberto,
            'saldo_faturas': saldo_faturas,
            'saldo_total': saldo_total,
            'pedidos_contagem': Pedido.objects.filter(cliente=cliente).count()
        })

    return render(request, 'caderninho/admin_clientes.html', {'clientes_list': clientes_list})


@staff_member_required
def admin_cliente_criar(request):
    """
    Cadastro rápido de novo cliente pelo admin.
    """
    if request.method == 'POST':
        form = ClienteForm(request.POST)
        if form.is_valid():
            cliente = form.save()
            messages.success(request, f"Cliente '{cliente.nome}' cadastrado com sucesso!")
            return redirect('admin_clientes')
    else:
        form = ClienteForm()
    return render(request, 'caderninho/admin_cliente_form.html', {'form': form, 'titulo': 'Cadastrar Cliente'})


@staff_member_required
def admin_cliente_detalhes(request, cliente_id):
    """
    Visualiza as compras e fechamentos de um cliente específico.
    Permite rodar o fechamento mensal e gerar cobrança via WhatsApp.
    """
    cliente = get_object_or_404(Cliente, id=cliente_id)
    
    # Histórico de pedidos
    pedidos = Pedido.objects.filter(cliente=cliente).order_by('-data_criacao')
    
    # Fechamentos consolidados
    fechamentos = FechamentoMensal.objects.filter(cliente=cliente).order_by('-ano', '-mes')

    # Calcula quanto está aberto (pedidos em aberto que ainda não foram faturados)
    pedidos_abertos_nfechados = Pedido.objects.filter(
        cliente=cliente,
        status_financeiro='ABERTO',
        fechamento_mensal__isnull=True
    ).exclude(status='CANCELADO')

    saldo_aberto_atual = Decimal("0.00")
    for p in pedidos_abertos_nfechados:
        saldo_aberto_atual += p.total_pedido

    # WhatsApp Link para faturas não pagas
    fechamentos_com_link = []
    for f in fechamentos:
        whatsapp_link = ""
        if not f.pago:
            try:
                whatsapp_link = gerar_mensagem_whatsapp(f)
            except Exception:
                whatsapp_link = "#"
        fechamentos_com_link.append({
            'obj': f,
            'whatsapp_link': whatsapp_link
        })

    hoje = timezone.localtime(timezone.now())

    return render(request, 'caderninho/admin_cliente_detalhes.html', {
        'cliente': cliente,
        'pedidos': pedidos,
        'fechamentos_com_link': fechamentos_com_link,
        'saldo_aberto_atual': saldo_aberto_atual,
        'ano_atual': hoje.year,
        'mes_atual': hoje.month
    })


@staff_member_required
def admin_cliente_fechar_mes(request, cliente_id):
    """
    Aciona a lógica de fechamento mensal manual para o cliente.
    """
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == 'POST':
        mes_str = request.POST.get('mes')
        ano_str = request.POST.get('ano')
        
        try:
            mes = int(mes_str)
            ano = int(ano_str)
            fechamento = fechar_mes_cliente(cliente, ano, mes)
            messages.success(request, f"Caderninho de {mes:02d}/{ano} fechado com sucesso. Total: R$ {fechamento.total_devedor}")
        except ValidationError as e:
            messages.warning(request, f"Aviso: {e.message}")
        except Exception as e:
            messages.error(request, f"Erro: {str(e)}")
            
    return redirect('admin_cliente_detalhes', cliente_id=cliente.id)


@staff_member_required
def admin_fechamento_pagar(request, fechamento_id):
    """
    Marca o fechamento e as faturas correspondentes como Pagas.
    """
    fechamento = get_object_or_404(FechamentoMensal, id=fechamento_id)
    marcar_fechamento_como_pago(fechamento)
    messages.success(request, f"Fechamento {fechamento.mes:02d}/{fechamento.ano} do cliente {fechamento.cliente.nome} liquidado com sucesso.")
    return redirect('admin_cliente_detalhes', cliente_id=fechamento.cliente.id)


@staff_member_required
def admin_cliente_editar(request, cliente_id):
    """
    Formulário para editar cadastro de um cliente (incluindo alteração/reset de senha).
    """
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == 'POST':
        form = ClienteForm(request.POST, instance=cliente)
        if form.is_valid():
            form.save()
            messages.success(request, f"Cadastro do cliente '{cliente.nome}' atualizado com sucesso!")
            return redirect('admin_cliente_detalhes', cliente_id=cliente.id)
    else:
        form = ClienteForm(instance=cliente)
    return render(request, 'caderninho/admin_cliente_form.html', {'form': form, 'titulo': 'Editar Cliente', 'cliente': cliente})


@staff_member_required
def admin_cliente_reset_senha(request, cliente_id):
    """
    Realiza a redefinição de senha rápida diretamente pelo card do cliente.
    """
    cliente = get_object_or_404(Cliente, id=cliente_id)
    if request.method == 'POST':
        nova_senha = request.POST.get('nova_senha', '').strip()
        if nova_senha:
            cliente.senha = nova_senha  # Criptografada no save() automaticamente
            cliente.save()
            messages.success(request, f"Senha do cliente '{cliente.nome}' redefinida com sucesso!")
        else:
            messages.error(request, "A senha não pode ser vazia.")
    return redirect('admin_cliente_detalhes', cliente_id=cliente.id)


from django.contrib.auth import login as auth_login
from django.contrib.auth.forms import AuthenticationForm

def admin_login(request):
    """
    Login customizado para o painel de administração com design premium e gradiente.
    """
    if request.user.is_authenticated and request.user.is_staff:
        return redirect('admin_dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if user is not None and user.is_staff:
                auth_login(request, user)
                next_url = request.GET.get('next', 'admin_dashboard')
                return redirect(next_url)
            else:
                messages.error(request, "Acesso restrito para administradores.")
        else:
            messages.error(request, "Usuário ou senha incorretos.")
    else:
        form = AuthenticationForm()

    return render(request, 'caderninho/admin_login.html', {'form': form})

