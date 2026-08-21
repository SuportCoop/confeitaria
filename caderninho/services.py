import re
import urllib.parse
import unicodedata
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, F, Count
from django.utils import timezone
from django.core.exceptions import ValidationError

from .models import (
    Cliente,
    Produto,
    Pedido,
    ItemPedido,
    FechamentoMensal,
    ConfiguracaoGeral
)


# =====================================================================
# 1. GERADOR PIX COPIA E COLA (Padrão EMV / Central Bank of Brazil)
# =====================================================================

def clean_ascii(text: str) -> str:
    """
    Remove acentos e caracteres especiais, mantendo apenas letras,
    números, espaços e limitando o tamanho para conformidade do PIX.
    """
    nfkd = unicodedata.normalize('NFKD', text)
    cleaned = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r'[^a-zA-Z0-9 ]', '', cleaned)


def crc16_ccitt(data: str) -> str:
    """
    Calcula o checksum CRC16-CCITT (polinômio 0x1021, valor inicial 0xFFFF).
    """
    crc = 0xFFFF
    for char in data.encode('utf-8'):
        crc ^= (char << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc <<= 1
            crc &= 0xFFFF
    return f"{crc:04X}"


def format_emv_field(tag: str, value: str) -> str:
    """
    Formata um campo EMV no padrão [Tag (2 bytes)][Tamanho (2 bytes)][Valor].
    """
    length = f"{len(value):02d}"
    return f"{tag}{length}{value}"


def generate_static_pix_payload(
    chave_pix: str,
    nome_titular: str,
    cidade_titular: str,
    valor: Decimal
) -> str:
    """
    Gera a string Pix Copia e Cola estática de acordo com a especificação do BC.
    """
    # 00 - Payload Format Indicator
    payload_format = format_emv_field("00", "01")

    # 26 - Merchant Account Information (Pix)
    gui = format_emv_field("00", "br.gov.bcb.pix")
    key = format_emv_field("01", chave_pix)
    merchant_info = format_emv_field("26", f"{gui}{key}")

    # 52 - Merchant Category Code
    mcc = format_emv_field("52", "0000")

    # 53 - Transaction Currency (986 para BRL)
    currency = format_emv_field("53", "986")

    # 54 - Transaction Amount (opcional, mas incluído se > 0)
    amount_str = f"{valor:.2f}"
    amount = format_emv_field("54", amount_str) if valor > 0 else ""

    # 58 - Country Code
    country = format_emv_field("58", "BR")

    # 59 - Merchant Name (Max 25 caracteres, limpo)
    name_clean = clean_ascii(nome_titular)[:25].strip()
    merchant_name = format_emv_field("59", name_clean)

    # 60 - Merchant City (Max 15 caracteres, limpo)
    city_clean = clean_ascii(cidade_titular)[:15].strip()
    merchant_city = format_emv_field("60", city_clean)

    # 62 - Additional Data Field Template
    tx_id = format_emv_field("05", "***")  # Pix estático sem ID definido
    additional_data = format_emv_field("62", tx_id)

    # Agrupa a primeira parte da string
    part1 = f"{payload_format}{merchant_info}{mcc}{currency}{amount}{country}{merchant_name}{merchant_city}{additional_data}"

    # 63 - CRC16 (A tag é "63", o tamanho é "04", e o valor são os 4 caracteres do CRC)
    crc_placeholder = "6304"
    data_to_crc = f"{part1}{crc_placeholder}"
    checksum = crc16_ccitt(data_to_crc)

    return f"{part1}6304{checksum}"


# =====================================================================
# 2. LOGICA DE NEGOCIO (SERVICES)
# =====================================================================

def criar_pedido(
    cliente: Cliente,
    itens_data: list,
    taxa_entrega: Decimal = Decimal("0.00"),
    observacoes: str = ""
) -> Pedido:
    """
    Cria um pedido e seus itens associados sob uma transação atômica.
    Garante a validação de estoque diário (limite de produção) usando
    select_for_update para evitar condições de corrida (Race Conditions).

    `itens_data` deve ser uma lista de dicionários: [{'produto_id': int, 'quantidade': int}]
    """
    hoje = timezone.localtime(timezone.now()).date()

    with transaction.atomic():
        # 1. Criar o pedido
        pedido = Pedido.objects.create(
            cliente=cliente,
            taxa_entrega=taxa_entrega,
            observacoes=observacoes,
            status='PENDENTE',
            status_financeiro='ABERTO'
        )

        for item in itens_data:
            prod_id = item['produto_id']
            qty = int(item['quantidade'])

            if qty <= 0:
                raise ValidationError("A quantidade de itens deve ser maior que zero.")

            # Bloqueia a linha do produto no banco para escrita/leitura concorrente
            produto = Produto.objects.select_for_update().get(id=prod_id)

            if not produto.disponivel_hoje:
                raise ValidationError(f"O produto '{produto.nome}' não está disponível hoje.")

            # Verifica limite de produção se configurado
            if produto.limite_diario > 0:
                # Soma quantidade já vendida desse produto hoje (pedidos não cancelados)
                quantidade_vendida_hoje = ItemPedido.objects.filter(
                    produto=produto,
                    pedido__data_criacao__date=hoje
                ).exclude(
                    pedido__status='CANCELADO'
                ).aggregate(total=Sum('quantidade'))['total'] or 0

                limite_restante = produto.limite_diario - quantidade_vendida_hoje

                if qty > limite_restante:
                    raise ValidationError(
                        f"Limite de produção diário excedido para '{produto.nome}'. "
                        f"Disponível hoje: {limite_restante} unidades (solicitado: {qty})."
                    )

            # Criar item de pedido
            ItemPedido.objects.create(
                pedido=pedido,
                produto=produto,
                quantidade=qty,
                preco_unitario=produto.preco  # Congela o preço unitário
            )

        return pedido


def fechar_mes_cliente(cliente: Cliente, ano: int, mes: int) -> FechamentoMensal:
    """
    Consolida as compras de um cliente que estão abertas e não canceladas
    em um determinado mês/ano, gerando o FechamentoMensal correspondente.
    """
    with transaction.atomic():
        # Busca pedidos em aberto do cliente criados no mês/ano que não estejam cancelados
        pedidos_abertos = Pedido.objects.filter(
            cliente=cliente,
            status_financeiro='ABERTO',
            data_criacao__year=ano,
            data_criacao__month=mes,
            fechamento_mensal__isnull=True
        ).exclude(status='CANCELADO')

        if not pedidos_abertos.exists():
            raise ValidationError(
                f"Não há pedidos em aberto para {cliente.nome} em {mes:02d}/{ano}."
            )

        # Calcula o valor de todos os itens + taxa_entrega dos pedidos
        total_acumulado = Decimal("0.00")
        for pedido in pedidos_abertos:
            total_acumulado += pedido.total_pedido

        # Cria ou atualiza o FechamentoMensal
        fechamento, created = FechamentoMensal.objects.update_or_create(
            cliente=cliente,
            ano=ano,
            mes=mes,
            defaults={
                'total_devedor': total_acumulado,
                'pago': False,
                'data_fechamento': timezone.now()
            }
        )

        # Associa todos os pedidos processados a este fechamento
        pedidos_abertos.update(fechamento_mensal=fechamento)

        return fechamento


def marcar_fechamento_como_pago(fechamento: FechamentoMensal) -> None:
    """
    Marca o fechamento mensal e todos os pedidos associados a ele como pagos.
    """
    with transaction.atomic():
        fechamento.pago = True
        fechamento.data_pagamento = timezone.now()
        fechamento.save()

        # Atualiza todos os pedidos associados para pagos
        fechamento.pedidos.all().update(status_financeiro='PAGO')


def gerar_mensagem_whatsapp(fechamento: FechamentoMensal) -> str:
    """
    Gera a URL de envio do WhatsApp contendo o detalhamento da conta do cliente
    e o código Pix Copia e Cola.
    """
    config = ConfiguracaoGeral.get_solo()

    # Agrupa detalhes das compras do fechamento
    # Para detalhar os itens consumidos:
    detalhes_linhas = []
    total_taxas = Decimal("0.00")

    pedidos = fechamento.pedidos.all().prefetch_related('itens__produto')
    for ped in pedidos:
        total_taxas += ped.taxa_entrega
        data_str = ped.data_criacao.strftime('%d/%m')
        for item in ped.itens.all():
            detalhes_linhas.append(
                f"• {data_str} | {item.quantidade}x {item.produto.nome} (R$ {item.preco_unitario} un.)"
            )

    detalhes_str = "\n".join(detalhes_linhas)
    periodo_str = f"{fechamento.mes:02d}/{fechamento.ano}"

    # Gera o PIX Copia e Cola para o fechamento
    pix_copia_cola = generate_static_pix_payload(
        chave_pix=config.chave_pix,
        nome_titular=config.nome_titular,
        cidade_titular=config.cidade_titular,
        valor=fechamento.total_devedor
    )

    # Formata a mensagem com base no template
    mensagem = config.mensagem_padrao_whatsapp.format(
        nome=fechamento.cliente.nome,
        periodo=periodo_str,
        detalhes=detalhes_str,
        taxa_entrega=f"{total_taxas:.2f}",
        total=f"{fechamento.total_devedor:.2f}",
        tipo_chave=config.get_tipo_chave_pix_display(),
        chave_pix=config.chave_pix,
        titular=config.nome_titular,
        copia_cola=pix_copia_cola
    )

    # Limpa caracteres extras e formata número de whatsapp
    whatsapp_limpo = re.sub(r'\D', '', fechamento.cliente.whatsapp)

    # URL encode na mensagem
    mensagem_encoded = urllib.parse.quote(mensagem)

    return f"https://wa.me/{whatsapp_limpo}?text={mensagem_encoded}"


def gerar_mensagem_whatsapp_pedido(pedido: Pedido) -> str:
    """
    Gera a URL de envio do WhatsApp contendo o detalhamento de um pedido individual.
    """
    config = ConfiguracaoGeral.get_solo()

    detalhes_linhas = []
    for item in pedido.itens.all():
        detalhes_linhas.append(
            f"• {item.quantidade}x {item.produto.nome} (R$ {item.preco_unitario} un.)"
        )

    detalhes_str = "\n".join(detalhes_linhas)

    # Gera o PIX Copia e Cola para o valor desse pedido específico
    pix_copia_cola = generate_static_pix_payload(
        chave_pix=config.chave_pix,
        nome_titular=config.nome_titular,
        cidade_titular=config.cidade_titular,
        valor=pedido.total_pedido
    )

    mensagem = (
        f"Olá, *{pedido.cliente.nome}*! 🌸\n\n"
        f"Segue o resumo do seu Pedido *#{pedido.id}* realizado em {pedido.data_criacao.strftime('%d/%m/%Y')}:\n\n"
        f"{detalhes_str}\n"
        f"Taxa de Entrega: R$ {pedido.taxa_entrega:.2f}\n"
        f"👉 *Total do Pedido:* R$ {pedido.total_pedido:.2f}\n\n"
        f"Para realizar o pagamento via PIX:\n"
        f"🔑 Chave PIX: `{config.chave_pix}`\n"
        f"Titular: *{config.nome_titular}*\n\n"
        f"Código PIX Copia e Cola:\n\n"
        f"`{pix_copia_cola}`\n\n"
        f"Ficamos no aguardo do comprovante. Muito obrigado! 😊"
    )

    whatsapp_limpo = re.sub(r'\D', '', pedido.cliente.whatsapp)
    mensagem_encoded = urllib.parse.quote(mensagem)

    return f"https://wa.me/{whatsapp_limpo}?text={mensagem_encoded}"


def obter_dados_dashboard(mes: int = None, ano: int = None) -> dict:
    """
    Calcula as agregações financeiras e operacionais para exibição no Dashboard.
    """
    hoje = timezone.localtime(timezone.now())
    mes = mes or hoje.month
    ano = ano or hoje.year

    total_clientes = Cliente.objects.filter(ativo=True).count()

    # Total de pedidos criados no mês de referência
    pedidos_mes = Pedido.objects.filter(
        data_criacao__year=ano,
        data_criacao__month=mes
    ).exclude(status='CANCELADO')

    total_pedidos = pedidos_mes.count()

    # Cálculo financeiro usando agregação Django ORM
    # Precisamos somar: taxa_entrega de todos os pedidos + subtotal de todos os itens do mês
    pago_pedidos = pedidos_mes.filter(status_financeiro='PAGO')
    aberto_pedidos = pedidos_mes.filter(status_financeiro='ABERTO')

    # Para somar os valores dos pedidos pagando e em aberto:
    def sum_pedidos_value(queryset):
        # Soma da taxa de entrega
        taxas = queryset.aggregate(total_taxa=Sum('taxa_entrega'))['total_taxa'] or Decimal("0.00")
        # Soma dos itens do pedido
        itens_total = queryset.annotate(
            total_item=Sum(F('itens__quantidade') * F('itens__preco_unitario'))
        ).aggregate(total_itens=Sum('total_item'))['total_itens'] or Decimal("0.00")
        return taxas + itens_total

    faturamento_pago = sum_pedidos_value(pago_pedidos)
    faturamento_receber = sum_pedidos_value(aberto_pedidos)

    # Top 5 itens mais vendidos
    itens_vendidos = ItemPedido.objects.filter(
        pedido__data_criacao__year=ano,
        pedido__data_criacao__month=mes
    ).exclude(
        pedido__status='CANCELADO'
    ).values(
        'produto__nome',
        'produto__categoria'
    ).annotate(
        total_quantidade=Sum('quantidade'),
        total_receita=Sum(F('quantidade') * F('preco_unitario'))
    ).order_by('-total_quantidade')[:5]

    return {
        'mes': mes,
        'ano': ano,
        'total_clientes': total_clientes,
        'total_pedidos': total_pedidos,
        'faturamento_pago': faturamento_pago,
        'faturamento_receber': faturamento_receber,
        'faturamento_total': faturamento_pago + faturamento_receber,
        'itens_vendidos': list(itens_vendidos),
    }
