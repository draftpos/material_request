from odoo import models, fields, api, _


class CustomPurchaseOrder(models.Model):

    def _default_request_to_ids(self):
        group = self.env.ref('material_requisition.group_custom_purchase_order_approver', raise_if_not_found=False)
        if group:
            return group.user_ids.ids
        return []
    _name = 'custom.purchase.order'
    _description = 'Purchase Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    vendor_id = fields.Many2one('res.partner', string='Supplier', tracking=True)
    purpose = fields.Selection([('Purchase', 'Purchase'), ('Internal', 'Internal')], string='Purpose', default='Purchase', tracking=True)
    requested_by_id = fields.Many2one('res.users', string='Requested By', default=lambda self: self.env.user, tracking=True)
    request_to_ids = fields.Many2many('res.users', string='Requested To', default=_default_request_to_ids)
    order_deadline = fields.Datetime(string='Order Deadline', tracking=True)
    transaction_date = fields.Datetime(string='Transaction Date', default=fields.Datetime.now, tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved By', tracking=True)
    requisition_id = fields.Many2one('material.requisition', string='Requisition Reference', readonly=True)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('rfq', 'RFQ'),
        ('rfq_sent', 'RFQ SENT'),
        ('po', 'Purchase Order')
    ,
        ('pending_approval', 'Pending Approval'),
        ('approved', 'Approved'),
        ('cancel', 'Cancelled'),
        ('rejected', 'Rejected')
    ], string='Status', readonly=True, default='draft', tracking=True)

    line_ids = fields.One2many('custom.purchase.order.line', 'document_id', string='Products', copy=True)
    
    amount_untaxed = fields.Monetary(string='Total Excl. Amount', store=True, readonly=True, compute='_amount_all', tracking=True)
    amount_tax = fields.Monetary(string='Taxes', store=True, readonly=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total Inclusive', store=True, readonly=True, compute='_amount_all')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    notes = fields.Html('Terms and Conditions')
    standard_po_id = fields.Many2one('purchase.order', string='Standard PO', readonly=True, copy=False)

    def action_submit(self):
        for rec in self:
            rec.state = 'pending_approval'

    def action_approve(self):
        for rec in self:
            rec.approved_by_id = self.env.user.id
            rec.state = 'approved'

    def action_reject(self):
        for rec in self:
            rec.state = 'rejected'

    def action_confirm(self):
        for rec in self:
            rec.state = 'po'
            
            # Link or Create standard PO and confirm it
            if not rec.standard_po_id:
                po_vals = {
                    'partner_id': rec.vendor_id.id,
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
                        'price_unit': line.price_unit,
                        'tax_ids': [(6, 0, line.taxes_id.ids)] if line.taxes_id else False,
                    }))
                rec.standard_po_id = self.env['purchase.order'].create(po_vals)
            
            if rec.standard_po_id.state in ['draft', 'sent']:
                rec.standard_po_id.button_confirm()

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancel'

    def action_create_invoice(self):
        self.ensure_one()
        inv_vals = {
            'vendor_id': self.vendor_id.id if self.vendor_id else False,
            'transaction_date': fields.Datetime.now(),
            'purchase_order_id': self.id,
            'line_ids': []
        }
        for line in self.line_ids:
            inv_vals['line_ids'].append((0, 0, {
                'product_id': line.product_id.id,
                'name': line.name or line.product_id.name,
                'product_qty': line.product_qty,
                'product_uom_id': line.product_uom_id.id,
                'price_unit': line.price_unit,
            }))
            
        inv = self.env['custom.purchase.invoice'].create(inv_vals)
        return {
            'name': _('Purchase Invoice'),
            'type': 'ir.actions.act_window',
            'res_model': 'custom.purchase.invoice',
            'view_mode': 'form',
            'res_id': inv.id,
            'target': 'current',
        }

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('custom.purchase.order') or _('New')
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

class CustomPurchaseOrderLine(models.Model):
    _name = 'custom.purchase.order.line'
    _description = 'Purchase Order Line'

    document_id = fields.Many2one('custom.purchase.order', string='Document Reference', required=True, ondelete='cascade', index=True, copy=False)
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

