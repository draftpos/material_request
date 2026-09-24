from odoo import models, fields, api, _


class CustomGrv(models.Model):

    def _default_request_to_ids(self):
        group = self.env.ref('material_request.group_custom_grv_approver', raise_if_not_found=False)
        if group:
            return group.user_ids.ids
        return []
    _name = 'custom.grv'
    _description = 'GRV'
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
    purchase_invoice_id = fields.Many2one('custom.purchase.invoice', string='Invoice Reference', readonly=True)
    
    receipt_type = fields.Selection([('full', 'Full Receipt'), ('partial', 'Partial Receipt')], string='Receipt Type', default='full', required=True, tracking=True)
    partial_reason = fields.Text(string='Reason for Partial Receipt', tracking=True)
    
    state = fields.Selection([
        ('draft', 'DRAFT'),
        ('done', 'DONE')
    ,
        ('pending_approval', 'Pending Approval'),
        ('approved', 'Approved'),
        ('cancel', 'Cancelled'),
        ('rejected', 'Rejected')
    ], string='Status', readonly=True, default='draft', tracking=True)

    line_ids = fields.One2many('custom.grv.line', 'document_id', string='Products', copy=True)
    
    amount_untaxed = fields.Monetary(string='Total Excl. Amount', store=True, readonly=True, compute='_amount_all', tracking=True)
    amount_tax = fields.Monetary(string='Taxes', store=True, readonly=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total Inclusive', store=True, readonly=True, compute='_amount_all')
    currency_id = fields.Many2one('res.currency', string='Currency', default=lambda self: self.env.company.currency_id)
    company_id = fields.Many2one('res.company', string='Company', default=lambda self: self.env.company)
    notes = fields.Html('Terms and Conditions')

    standard_picking_id = fields.Many2one('stock.picking', string='Standard Receipt', readonly=True, copy=False)

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
        from odoo.exceptions import UserError
        for rec in self:
            if rec.receipt_type == 'partial' and not rec.partial_reason:
                raise UserError(_("Please provide a Reason for Partial Receipt."))
            if rec.receipt_type == 'full':
                if any(line.product_qty < line.demand_qty for line in rec.line_ids):
                    raise UserError(_("You selected 'Full Receipt', but some quantities are less than the original demand. Please select 'Partial Receipt' instead or correct the quantities."))
            
            rec.state = 'done'
            
            # Find the material requisition linked to this GRV
            requisition = self.env['material.requisition'].search([('picking_ids', 'in', rec.id)], limit=1)
            
            # Handle Partial Receipt by creating a backorder GRV for the remaining balance
            if rec.receipt_type == 'partial':
                backorder_lines = []
                for line in rec.line_ids:
                    if line.product_qty < line.demand_qty:
                        backorder_qty = line.demand_qty - line.product_qty
                        backorder_lines.append((0, 0, {
                            'product_id': line.product_id.id,
                            'name': line.name,
                            'demand_qty': backorder_qty,
                            'product_qty': backorder_qty,
                            'product_uom_id': line.product_uom_id.id,
                            'price_unit': line.price_unit,
                            'taxes_id': [(6, 0, line.taxes_id.ids)] if line.taxes_id else False,
                            'discount': line.discount,
                        }))
                
                if backorder_lines:
                    backorder_vals = {
                        'vendor_id': rec.vendor_id.id,
                        'purpose': rec.purpose,
                        'requested_by_id': rec.requested_by_id.id,
                        'request_to_ids': [(6, 0, rec.request_to_ids.ids)] if rec.request_to_ids else False,
                        'order_deadline': rec.order_deadline,
                        'transaction_date': rec.transaction_date,
                        'approved_by_id': rec.approved_by_id.id,
                        'currency_id': rec.currency_id.id,
                        'company_id': rec.company_id.id,
                        'notes': rec.notes,
                        'line_ids': backorder_lines,
                        'receipt_type': 'full',
                    }
                    backorder = self.env['custom.grv'].create(backorder_vals)
                    if requisition:
                        requisition.write({'picking_ids': [(4, backorder.id)]})

            # Auto-generate Custom Purchase Invoice for the received items
            inv_vals = {
                'vendor_id': rec.vendor_id.id,
                'transaction_date': fields.Datetime.now(),
                'line_ids': [],
                'state': 'draft',
            }
            if requisition:
                # Assuming the requisition is linked to a standard PO
                po = self.env['purchase.order'].search([('material_requisition_id', '=', requisition.id)], limit=1)
                if po:
                    inv_vals['purchase_order_id'] = po.id

            for line in rec.line_ids:
                if line.product_qty > 0:
                    inv_vals['line_ids'].append((0, 0, {
                        'product_id': line.product_id.id,
                        'name': line.name,
                        'product_qty': line.product_qty,
                        'product_uom_id': line.product_uom_id.id,
                        'price_unit': line.price_unit,
                    }))
                    
            if inv_vals['line_ids']:
                invoice = self.env['custom.purchase.invoice'].create(inv_vals)
                rec.purchase_invoice_id = invoice.id
                if requisition:
                    requisition.write({'invoice_ids': [(4, invoice.id)]})
                    
            # We skip the standard picking validation for now because we use standard pickings only for Odoo inventory
            # If standard picking needs to be validated, it should be done using the standard receipt.

    def action_cancel(self):
        for rec in self:
            rec.state = 'cancel'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('custom.grv') or _('New')
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

class CustomGrvLine(models.Model):
    _name = 'custom.grv.line'
    _description = 'GRV Line'

    document_id = fields.Many2one('custom.grv', string='Document Reference', required=True, ondelete='cascade', index=True, copy=False)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    name = fields.Text(string='Description', required=True)
    demand_qty = fields.Float(string='Original Demand', digits='Product Unit of Measure', readonly=True)
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

