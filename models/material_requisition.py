from odoo import models, fields, api, _
from odoo.exceptions import UserError

class MaterialRequisition(models.Model):

    def _default_request_to_ids(self):
        group = self.env.ref('material_requisition.group_material_requisition_approver', raise_if_not_found=False)
        if group:
            return group.user_ids.ids
        return []
    _name = 'material.requisition'
    _description = 'Requisition'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, index=True, default=lambda self: _('New'))
    transaction_date = fields.Datetime(string='Transaction Date', default=fields.Datetime.now, required=True, tracking=True)
    purpose = fields.Selection([
        ('purchase', 'Purchase'),
        ('transfer', 'Material Transfer'),
        ('issue', 'Material Issue'),
        ('manufacture', 'Manufacture'),
        ('customer', 'Customer Provided')
    ], string='Purpose', default='purchase', tracking=True)
    
    vendor_id = fields.Many2one('res.partner', string='Supplier', tracking=True)
    vendor_reference = fields.Char(string='Supplier Reference', tracking=True)
    order_deadline = fields.Datetime(string='Order Deadline', tracking=True)
    expected_arrival = fields.Datetime(string='Expected Arrival', tracking=True)
    ask_confirmation = fields.Boolean(string='Ask Confirmation')
    deliver_to_id = fields.Many2one('stock.picking.type', string='Deliver To', domain="[('code', '=', 'incoming')]")
    receipt_location_id = fields.Many2one('stock.location', string='Receipt Location')
    company_id = fields.Many2one('res.company', 'Company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', 'Currency', related='company_id.currency_id')

    requested_by_id = fields.Many2one('res.users', string='Requested By', default=lambda self: self.env.user, tracking=True)
    approved_by_id = fields.Many2one('res.users', string='Approved By', tracking=True)
    request_to_ids = fields.Many2many('res.users', string='Requested To', tracking=True, default=_default_request_to_ids)
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending Approval'),
        ('approved', 'Approved'),
        ('done', 'Done')
    ], string='Status', default='draft', tracking=True)
    
    list_state = fields.Char(string='Status Summary', compute='_compute_list_state')

    @api.depends('state', 'approved_by_id', 'request_to_ids')
    def _compute_list_state(self):
        for req in self:
            if req.state == 'draft':
                req.list_state = 'Draft'
            elif req.state == 'pending':
                users = ', '.join(req.request_to_ids.mapped('name'))
                req.list_state = f"Pending Approval by {users}" if users else "Pending Approval"
            elif req.state == 'approved':
                req.list_state = f"Approved by {req.approved_by_id.name}" if req.approved_by_id else "Approved"
            elif req.state == 'done':
                req.list_state = 'Done'
            else:
                req.list_state = dict(self._fields['state'].selection).get(req.state, req.state)
    
    line_ids = fields.One2many('material.requisition.line', 'requisition_id', string='Products', copy=True)
    
    amount_untaxed = fields.Monetary(string='Total Excl. Amount', store=True, readonly=True, compute='_amount_all', tracking=True)
    amount_tax = fields.Monetary(string='Taxes', store=True, readonly=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total Inclusive', store=True, readonly=True, compute='_amount_all')
    notes = fields.Html('Terms and Conditions')

    # Linked documents
    rfq_ids = fields.Many2many('custom.purchase.order', string='Purchase Orders')
    rfq_count = fields.Integer(compute='_compute_rfq_count')
    invoice_ids = fields.Many2many('custom.purchase.invoice', string='Invoices', copy=False)
    invoice_count = fields.Integer(compute='_compute_invoice_count')
    picking_ids = fields.Many2many('custom.grv', string='Receipts / GRVs', copy=False)
    picking_count = fields.Integer(compute='_compute_picking_count')
    quote_ids = fields.Many2many('custom.supplier.quote', string='Supplier Quotes', copy=False)
    quote_count = fields.Integer(compute='_compute_quote_count')
    
    @api.depends('quote_ids')
    def _compute_quote_count(self):
        for req in self:
            req.quote_count = len(req.quote_ids)

    @api.depends('rfq_ids')
    def _compute_rfq_count(self):
        for req in self:
            req.rfq_count = len(req.rfq_ids)

    @api.depends('invoice_ids')
    def _compute_invoice_count(self):
        for req in self:
            req.invoice_count = len(req.invoice_ids)

    @api.depends('picking_ids')
    def _compute_picking_count(self):
        for req in self:
            req.picking_count = len(req.picking_ids)

    def action_approve(self):
        for rec in self:
            rec.state = 'approved' if 'approved' in dict(self._fields['state'].selection).keys() else 'done'

    def action_reject(self):
        for rec in self:
            rec.state = 'rejected'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('material.requisition') or _('New')
        return super().create(vals_list)

    @api.depends('line_ids.price_subtotal')
    def _amount_all(self):
        for req in self:
            amount_untaxed = amount_tax = 0.0
            for line in req.line_ids:
                amount_untaxed += line.price_subtotal
                price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
                taxes = line.taxes_id.compute_all(price, line.currency_id, line.product_qty, product=line.product_id, partner=req.company_id.partner_id)
                amount_tax += sum(t.get('amount', 0.0) for t in taxes.get('taxes', []))
            req.update({
                'amount_untaxed': req.currency_id.round(amount_untaxed) if req.currency_id else amount_untaxed,
                'amount_tax': req.currency_id.round(amount_tax) if req.currency_id else amount_tax,
                'amount_total': amount_untaxed + amount_tax,
            })

    def action_submit(self):
        self.write({'state': 'pending'})

    def action_approve(self):
        self.write({
            'state': 'approved',
            'approved_by_id': self.env.user.id
        })

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.write({'state': 'draft'})
        
    def action_done(self):
        self.write({'state': 'done'})

    def action_create_rfq(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Hey, you have to wait for the Requisition to be approved first!"))
        
        rfq_vals = {
            'transaction_date': self.transaction_date or fields.Datetime.now(),
            'order_deadline': self.order_deadline,
            'requested_by_id': self.requested_by_id.id,
            'requisition_id': self.id,
            'line_ids': []
        }
        for line in self.line_ids:
            rfq_vals['line_ids'].append((0, 0, {
                'product_id': line.product_id.id,
                'name': line.name or line.product_id.name,
                'product_qty': line.product_qty,
                'product_uom_id': line.product_uom_id.id,
                'price_unit': line.price_unit,
            }))
            
        rfq = self.env['custom.rfq'].create(rfq_vals)
        # Note: we should link this RFQ to the material requisition if needed.
        # But for now, we just return the view for the new RFQ.
        return {
            'name': _('RFQ'),
            'type': 'ir.actions.act_window',
            'res_model': 'custom.rfq',
            'view_mode': 'form',
            'res_id': rfq.id,
            'target': 'current',
        }

    def action_create_po(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Hey, you have to wait for the Requisition to be approved first!"))
        if not self.line_ids:
            raise UserError(_("Please add some items first."))
            
        po_vals = {
            'vendor_id': self.vendor_id.id if self.vendor_id else False,
            'order_deadline': self.order_deadline or fields.Datetime.now(),
            'requisition_id': self.id,
            'line_ids': []
        }
        for line in self.line_ids:
            po_vals['line_ids'].append((0, 0, {
                'product_id': line.product_id.id,
                'product_qty': line.product_qty,
                'product_uom_id': line.product_uom_id.id,
                'price_unit': line.price_unit,
                'name': line.product_id.name,
            }))
            
        po = self.env['custom.purchase.order'].create(po_vals)
        self.rfq_ids = [(4, po.id)]
        self.write({'state': 'po'})
        return self.action_view_rfqs()

    def action_create_invoice(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Hey, you have to wait for the Requisition to be approved first!"))
        if not self.line_ids:
            raise UserError(_("Please add some items first."))
            
        inv_vals = {
            'vendor_id': self.vendor_id.id if self.vendor_id else False,
            'transaction_date': fields.Datetime.now(),
            'purchase_order_id': self.rfq_ids[0].id if self.rfq_ids else False,
            'line_ids': []
        }
        for line in self.line_ids:
            inv_vals['line_ids'].append((0, 0, {
                'product_id': line.product_id.id,
                'product_qty': line.product_qty,
                'product_uom_id': line.product_uom_id.id,
                'price_unit': line.price_unit,
                'name': line.product_id.name,
            }))
            
        inv = self.env['custom.purchase.invoice'].create(inv_vals)
        self.invoice_ids = [(4, inv.id)]
        
        return self.action_view_invoices()

    def action_create_grv(self):
        self.ensure_one()
        if self.state != 'approved':
            raise UserError(_("Hey, you have to wait for the Requisition to be approved first!"))
        if not self.line_ids:
            raise UserError(_("Please add some items first."))
            
        grv_vals = {
            'vendor_id': self.vendor_id.id if self.vendor_id else False,
            'transaction_date': fields.Datetime.now(),
            'line_ids': []
        }
        
        for line in self.line_ids:
            grv_vals['line_ids'].append((0, 0, {
                'name': line.product_id.name,
                'product_id': line.product_id.id,
                'product_qty': line.product_qty,
                'product_uom_id': line.product_uom_id.id,
                'price_unit': line.price_unit,
            }))
            
        grv = self.env['custom.grv'].create(grv_vals)
        self.picking_ids = [(4, grv.id)]
        
        return self.action_view_pickings()

    # Smart button actions
    def action_view_rfqs(self):
        self.ensure_one()
        return {
            'name': _('Purchase Orders'),
            'type': 'ir.actions.act_window',
            'res_model': 'custom.purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.rfq_ids.ids)],
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'name': _('Purchase Invoices'),
            'type': 'ir.actions.act_window',
            'res_model': 'custom.purchase.invoice',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
        }

    def action_view_pickings(self):
        self.ensure_one()
        return {
            'name': _('GRVs'),
            'type': 'ir.actions.act_window',
            'res_model': 'custom.grv',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.picking_ids.ids)],
        }

class MaterialRequisitionLine(models.Model):
    _name = 'material.requisition.line'
    _description = 'Requisition Line'

    requisition_id = fields.Many2one('material.requisition', string='Requisition', required=True, ondelete='cascade')
    product_id = fields.Many2one('product.product', string='Product', required=True)
    name = fields.Text(string='Description')
    product_qty = fields.Float(string='Quantity', required=True, default=1.0)
    product_uom_id = fields.Many2one('uom.uom', string='UOM', required=True)
    warehouse_id = fields.Many2one('stock.warehouse', string='Target Warehouse')
    
    price_unit = fields.Float(string='Unit Price', required=True, digits='Product Price', default=0.0)
    taxes_id = fields.Many2many('account.tax', string='Taxes', domain=['|', ('active', '=', False), ('active', '=', True)])
    discount = fields.Float(string='Disc.%', digits='Discount', default=0.0)
    price_subtotal = fields.Monetary(compute='_compute_amount', string='Amount', store=True)
    currency_id = fields.Many2one(related='requisition_id.currency_id', store=True, string='Currency', readonly=True)

    date_planned = fields.Datetime(string='Expected Arrival')
    propagate_cancel = fields.Boolean(string='Propagate cancellation')
    display_type = fields.Selection([('line_section', "Section"), ('line_note', "Note")], default=False, help="Technical field for UX purpose.")

    @api.depends('product_qty', 'price_unit', 'taxes_id', 'discount')
    def _compute_amount(self):
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.taxes_id.compute_all(price, line.currency_id, line.product_qty, product=line.product_id, partner=line.requisition_id.company_id.partner_id)
            line.price_subtotal = taxes['total_excluded']

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if not self.product_id:
            return
        self.name = self.product_id.display_name
        if self.product_id.description_purchase:
            self.name += '\n' + self.product_id.description_purchase
        self.price_unit = self.product_id.standard_price
        if self.product_id:
            self.product_uom_id = self.product_id.uom_id
        if self.requisition_id.company_id:
            taxes = self.product_id.supplier_taxes_id.filtered(lambda r: r.company_id == self.requisition_id.company_id)
            self.taxes_id = taxes

class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'
    
    material_requisition_id = fields.Many2one('material.requisition', string='Requisition Reference', copy=False)
