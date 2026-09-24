from odoo import models, fields, api, _


class CustomRfqSupplier(models.Model):
    _name = 'custom.rfq.supplier'
    _description = 'RFQ Supplier'

    rfq_id = fields.Many2one('custom.rfq', string='RFQ', required=True, ondelete='cascade')
    partner_id = fields.Many2one('res.partner', string='Supplier', required=True)
    name = fields.Char(related='partner_id.name', string='Name', readonly=True)
    email = fields.Char(related='partner_id.email', string='Email', readonly=True)
    phone = fields.Char(related='partner_id.phone', string='Phone', readonly=True)
    send_email = fields.Boolean(string='Send Email', default=True)
    quote_status = fields.Selection([
        ('pending', 'Pending'),
        ('received', 'Received'),
        ('rejected', 'Rejected'),
    ], string='Quote Status', default='pending')
    email_sent = fields.Boolean(string='Email Sent', default=False)


class CustomRfq(models.Model):
    _name = 'custom.rfq'
    _description = 'RFQ'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    supplier_ids = fields.One2many('custom.rfq.supplier', 'rfq_id', string='Suppliers')
    supplier_quote_ids = fields.One2many('custom.supplier.quote', 'rfq_id', string='Supplier Quotes')
    all_quotes_created = fields.Boolean(compute='_compute_all_quotes_created')

    @api.depends('supplier_ids', 'supplier_quote_ids')
    def _compute_all_quotes_created(self):
        for rec in self:
            all_suppliers = set(rec.supplier_ids.mapped('partner_id.id'))
            quoted_suppliers = set(rec.supplier_quote_ids.mapped('vendor_id.id'))
            rec.all_quotes_created = bool(all_suppliers) and all_suppliers.issubset(quoted_suppliers)

    purpose = fields.Selection([('Purchase', 'Purchase'), ('Internal', 'Internal')], string='Purpose', default='Purchase', tracking=True)
    requested_by_id = fields.Many2one('res.users', string='Requested By', default=lambda self: self.env.user, tracking=True)
    request_to_ids = fields.Many2many('res.users', string='Requested To')
    order_deadline = fields.Datetime(string='Deadline Date', tracking=True)
    transaction_date = fields.Datetime(string='Date', default=fields.Datetime.now, tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved By', tracking=True)
    
    state = fields.Selection([
        ('rfq', 'RFQ'),
        ('pending_approval', 'Pending Approval'),
        ('approved', 'Approved'),
        ('rfq_sent', 'RFQ SENT'),
        ('done', 'DONE'),
        ('cancel', 'Cancelled'),
        ('rejected', 'Rejected'),
    ], string='Status', readonly=True, default='rfq', tracking=True)

    line_ids = fields.One2many('custom.rfq.line', 'document_id', string='Products', copy=True)
    
    amount_untaxed = fields.Monetary(string='Total Excl. Amount', store=True, readonly=True, compute='_amount_all', tracking=True)
    amount_tax = fields.Monetary(string='Taxes', store=True, readonly=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total Inclusive', store=True, readonly=True, compute='_amount_all')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id)
    requisition_id = fields.Many2one('material.requisition', string='Requisition Reference', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    notes = fields.Html('Terms and Conditions')
    standard_po_ids = fields.Many2many('purchase.order', string='Standard RFQs', readonly=True, copy=False)

    def action_submit(self):
        for rec in self:
            rec.state = 'pending_approval'

    def action_approve(self):
        for rec in self:
            rec.approved_by_id = self.env.user.id
            rec.state = 'approved'
            
            # Create a standard Odoo RFQ (purchase.order) for each supplier
            standard_pos = self.env['purchase.order']
            for supplier in rec.supplier_ids:
                po_vals = {
                    'partner_id': supplier.partner_id.id,
                    'company_id': rec.company_id.id or self.env.company.id,
                    'date_order': rec.transaction_date or fields.Datetime.now(),
                    'origin': rec.name,
                    'order_line': [],
                }
                for line in rec.line_ids:
                    po_vals['order_line'].append((0, 0, {
                        'product_id': line.product_id.id,
                        'name': line.name or line.product_id.name,
                        'product_qty': line.product_qty,
                        'product_uom_id': line.product_uom_id.id if line.product_uom_id else line.product_id.uom_id.id,
                        'price_unit': line.price_unit or 0.0,
                        'tax_ids': [(6, 0, line.taxes_id.ids)] if line.taxes_id else False,
                    }))
                standard_pos |= self.env['purchase.order'].create(po_vals)
            
            rec.standard_po_ids = [(6, 0, standard_pos.ids)]

    def action_reject(self):
        for rec in self:
            rec.state = 'rejected'

    def action_confirm(self):
        for rec in self:
            rec.state = 'rfq_sent'

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancel'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('custom.rfq') or _('New')
        return super().create(vals_list)

    @api.depends('line_ids.price_subtotal')
    def _amount_all(self):
        for doc in self:
            amount_untaxed = amount_tax = 0.0
            for line in doc.line_ids:
                amount_untaxed += line.price_subtotal
                price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
                taxes = line.taxes_id.compute_all(price, line.currency_id, line.product_qty, product=line.product_id, partner=doc.company_id.partner_id)
                amount_tax += sum(t.get('amount', 0.0) for t in taxes.get('taxes', []))
            doc.update({
                'amount_untaxed': doc.currency_id.round(amount_untaxed) if doc.currency_id else amount_untaxed,
                'amount_tax': doc.currency_id.round(amount_tax) if doc.currency_id else amount_tax,
                'amount_total': amount_untaxed + amount_tax,
            })

class CustomRfqLine(models.Model):
    _name = 'custom.rfq.line'
    _description = 'RFQ Line'

    document_id = fields.Many2one('custom.rfq', string='Document Reference', required=True, ondelete='cascade', index=True, copy=False)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    name = fields.Text(string='Description', required=True)
    product_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom_id = fields.Many2one('uom.uom', string='UOM')
    price_unit = fields.Float(string='Unit Price', required=True, digits='Product Price')
    taxes_id = fields.Many2many('account.tax', string='Taxes', domain=['|', ('active', '=', False), ('active', '=', True)])
    discount = fields.Float(string='Discount (%)', digits='Discount', default=0.0)
    price_subtotal = fields.Monetary(compute='_compute_amount', string='Amount', store=True)
    currency_id = fields.Many2one(related='document_id.currency_id', store=True, string='Currency', readonly=True)

    @api.depends('product_qty', 'discount', 'price_unit', 'taxes_id')
    def _compute_amount(self):
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.taxes_id.compute_all(price, line.currency_id, line.product_qty, product=line.product_id, partner=line.document_id.company_id.partner_id)
            line.price_subtotal = taxes['total_excluded']

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if not self.product_id:
            return
        self.name = self.product_id.display_name
        if self.product_id.description_purchase:
            self.name += '\n' + self.product_id.description_purchase
        self.price_unit = self.product_id.standard_price
        self.product_uom_id = self.product_id.uom_id

