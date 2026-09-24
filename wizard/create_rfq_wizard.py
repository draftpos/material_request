from odoo import models, fields, api, _

class CreateRfqWizard(models.TransientModel):
    _name = 'wizard.create.rfq'
    _description = 'Create RFQ Wizard'

    requisition_id = fields.Many2one('material.requisition', string='Requisition', required=True)
    supplier_ids = fields.Many2many('res.partner', string='Suppliers', domain=[('supplier_rank', '>', 0)])

    def action_create_rfq(self):
        self.ensure_one()
        rfqs = self.env['custom.supplier.quote']
        
        for supplier in self.supplier_ids:
            po_vals = {
                'vendor_id': supplier.id,
                'order_deadline': self.requisition_id.order_deadline,
                'line_ids': []
            }
            
            for line in self.requisition_id.line_ids:
                po_vals['line_ids'].append((0, 0, {
                    'product_id': line.product_id.id,
                    'product_qty': line.product_qty,
                    'product_uom_id': line.product_uom_id.id,
                    'price_unit': 0.0,
                    'name': line.product_id.name,
                }))
                
            rfqs |= self.env['custom.supplier.quote'].create(po_vals)
            
        self.requisition_id.write({'state': 'rfq'})
        return self.requisition_id.action_view_rfqs()
