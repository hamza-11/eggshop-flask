import os
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, send_file
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy import func, case, and_
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
import pandas as pd
import io

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "dev-secret-key")
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///egg_store.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = "الرجاء تسجيل الدخول للوصول إلى هذه الصفحة."

# Models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(128))
    role = db.Column(db.String(20), nullable=False, default='seller') # 'admin' or 'seller'

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    phone = db.Column(db.String(30))
    notes = db.Column(db.Text)

    @property
    def balance(self):
        total_debt = db.session.query(func.sum(DebtTransaction.amount)).filter(
            DebtTransaction.customer_id == self.id,
            DebtTransaction.transaction_type == 'debt'
        ).scalar() or Decimal('0')
        total_payment = db.session.query(func.sum(DebtTransaction.amount)).filter(
            DebtTransaction.customer_id == self.id,
            DebtTransaction.transaction_type == 'payment'
        ).scalar() or Decimal('0')
        return total_debt - total_payment

class Product(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    stock = db.Column(db.Integer, default=0)
    price_wholesale = db.Column(db.Numeric(10,2), nullable=False, default=0)
    price_retail = db.Column(db.Numeric(10,2), nullable=False, default=0)
    notes = db.Column(db.Text)

class Sale(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=True)
    customer = db.relationship('Customer')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    total = db.Column(db.Numeric(10,2), default=0)
    paid_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    due_amount = db.Column(db.Numeric(10, 2), nullable=False, default=0)
    payment_type = db.Column(db.String(20), default="cash")
    notes = db.Column(db.Text)

class SaleItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'))
    sale = db.relationship('Sale', backref='items')
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'))
    product = db.relationship('Product')
    qty = db.Column(db.Integer, default=0)
    unit_price = db.Column(db.Numeric(10,2), default=0)
    cost_price = db.Column(db.Numeric(10,2), nullable=False, default=0)

class DebtTransaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customer.id'), nullable=False)
    customer = db.relationship('Customer', backref=db.backref('transactions', lazy=True, cascade="all, delete-orphan"))
    sale_id = db.Column(db.Integer, db.ForeignKey('sale.id'), nullable=True)
    sale = db.relationship('Sale')
    date = db.Column(db.DateTime, default=datetime.utcnow)
    transaction_type = db.Column(db.String(20), nullable=False) # 'debt' or 'payment'
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    description = db.Column(db.Text)

class DamagedProduct(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    product = db.relationship('Product')
    quantity = db.Column(db.Integer, nullable=False)
    date = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('ليس لديك الصلاحية للوصول لهذه الصفحة.')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# Auth routes
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user)
            return redirect(url_for('index'))
        else:
            flash('اسم المستخدم أو كلمة المرور غير صحيحة')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Home / dashboard
@app.route('/')
@login_required
def index():
    if current_user.role == 'seller':
        return redirect(url_for('fast_selling'))

    today = date.today()

    # 1. Sales and Profits for Today
    sales_today = db.session.query(
        func.sum(Sale.total).label('total_sales'),
        func.sum(case((Sale.payment_type == 'cash', Sale.total), else_=0)).label('cash_sales'),
        func.sum(case((Sale.payment_type == 'credit', Sale.total), else_=0)).label('credit_sales')
    ).filter(func.date(Sale.date) == today).first()

    profit_today_query = db.session.query(
        func.sum((SaleItem.unit_price - SaleItem.cost_price) * SaleItem.qty)
    ).join(Sale).filter(func.date(Sale.date) == today).scalar()
    profit_today = profit_today_query or Decimal('0')

    # 2. Total Unpaid Debts (from new ledger system)
    total_unpaid_debt_query = db.session.query(
        (func.sum(case((DebtTransaction.transaction_type == 'debt', DebtTransaction.amount), else_=0))) -
        (func.sum(case((DebtTransaction.transaction_type == 'payment', DebtTransaction.amount), else_=0)))
    ).scalar()
    total_unpaid_debt = total_unpaid_debt_query or Decimal('0')

    # 3. Low Stock Products
    low_stock_limit = 30
    low_stock_products = Product.query.filter(Product.stock <= low_stock_limit).order_by(Product.stock).all()
    low_stock_count = len(low_stock_products)

    # 4. Sale Items from Today
    recent_sale_items = SaleItem.query.join(Sale).filter(func.date(Sale.date) == today).order_by(Sale.date.desc()).all()

    # 5. Top 5 Customers with Highest Debt (from new ledger system)
    top_debtors_query = db.session.query(
        Customer,
        (func.sum(case((DebtTransaction.transaction_type == 'debt', DebtTransaction.amount), else_=0)) -
         func.sum(case((DebtTransaction.transaction_type == 'payment', DebtTransaction.amount), else_=0))).label('total_debt')
    ).join(DebtTransaction).group_by(Customer).order_by(db.text('total_debt DESC')).limit(5).all()
    top_debtors = [debtor for debtor in top_debtors_query if debtor.total_debt > 0]

    # 6. Damaged products for today
    damaged_today = db.session.query(func.sum(DamagedProduct.quantity)).filter(func.date(DamagedProduct.date) == today).scalar() or 0

    stats = {
        'total_sales_today': sales_today.total_sales or Decimal('0'),
        'cash_sales_today': sales_today.cash_sales or Decimal('0'),
        'credit_sales_today': sales_today.credit_sales or Decimal('0'),
        'profit_today': profit_today,
        'total_unpaid_debt': total_unpaid_debt,
        'low_stock_count': low_stock_count,
        'damaged_today': damaged_today
    }

    return render_template('index.html', 
                           stats=stats,
                           recent_sale_items=recent_sale_items,
                           top_debtors=top_debtors,
                           low_stock_products=low_stock_products)

# Customers CRUD
@app.route('/customers')
@login_required
@admin_required
def customers():
    customers = Customer.query.order_by(Customer.name).all()
    return render_template('customers.html', customers=customers)

@app.route('/customer/add', methods=['POST'])
@login_required
@admin_required
def add_customer():
    name = request.form['name']
    phone = request.form.get('phone')
    notes = request.form.get('notes')
    if not name:
        flash('اسم الزبون مطلوب')
        return redirect(url_for('customers'))
    c = Customer(name=name, phone=phone, notes=notes)
    db.session.add(c)
    db.session.commit()
    flash('تمت إضافة الزبون')
    return redirect(url_for('customers'))

@app.route('/customer/delete/<int:id>')
@login_required
@admin_required
def delete_customer(id):
    customer = Customer.query.get_or_404(id)
    if customer.balance > 0:
        flash(f'لا يمكن حذف الزبون لأن عليه دين مستحق قدره {customer.balance:.2f}. يرجى تسوية الحساب أولاً.')
        return redirect(url_for('customers'))
    
    # Disassociate from sales (optional, good practice)
    sales = Sale.query.filter_by(customer_id=id).all()
    for sale in sales:
        sale.customer_id = None
    
    db.session.delete(customer)
    db.session.commit()
    flash('تم حذف الزبون بنجاح.')
    return redirect(url_for('customers'))

@app.route('/customer/edit/<int:id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_customer(id):
    customer = Customer.query.get_or_404(id)
    if request.method == 'POST':
        customer.name = request.form['name']
        customer.phone = request.form.get('phone')
        customer.notes = request.form.get('notes')
        if not customer.name:
            flash('اسم الزبون مطلوب')
            return render_template('edit_customer.html', customer=customer)
        db.session.commit()
        flash('تم تحديث بيانات الزبون بنجاح.')
        return redirect(url_for('customers'))
    return render_template('edit_customer.html', customer=customer)

# Customer Ledger Routes
@app.route('/customer/<int:customer_id>/ledger')
@login_required
@admin_required
def customer_ledger(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    transactions = DebtTransaction.query.filter_by(customer_id=customer_id).order_by(DebtTransaction.date.desc()).all()
    return render_template('customer_ledger.html', customer=customer, transactions=transactions)

@app.route('/customer/add_transaction', methods=['POST'])
@login_required
@admin_required
def add_transaction():
    customer_id = request.form.get('customer_id')
    transaction_type = request.form.get('transaction_type')
    amount_str = request.form.get('amount')
    description = request.form.get('description')

    if not all([customer_id, transaction_type, amount_str]):
        flash('بيانات غير مكتملة.')
        return redirect(request.referrer or url_for('customers'))

    try:
        amount = Decimal(amount_str)
        if amount <= 0:
            flash('المبلغ يجب أن يكون أكبر من صفر.')
            return redirect(request.referrer)
    except Exception:
        flash('مبلغ غير صالح.')
        return redirect(request.referrer)

    customer = Customer.query.get(customer_id)
    if not customer:
        flash('الزبون غير موجود.')
        return redirect(url_for('customers'))

    new_transaction = DebtTransaction(
        customer_id=customer_id,
        transaction_type=transaction_type,
        amount=amount,
        description=description or f'معاملة يدوية - {transaction_type}'
    )
    db.session.add(new_transaction)
    db.session.commit()

    flash('تم تسجيل المعاملة بنجاح.')
    return redirect(url_for('customer_ledger', customer_id=customer_id))

@app.route('/all_debts')
@login_required
@admin_required
def all_debts():
    customers_with_debt_query = db.session.query(
        Customer,
        (func.sum(case((DebtTransaction.transaction_type == 'debt', DebtTransaction.amount), else_=0)) -
         func.sum(case((DebtTransaction.transaction_type == 'payment', DebtTransaction.amount), else_=0))).label('total_debt')
    ).join(DebtTransaction).group_by(Customer).order_by(db.text('total_debt DESC')).all()

    customers_with_debt = [c for c in customers_with_debt_query if c.total_debt > 0]
    total_unpaid = sum(c.total_debt for c in customers_with_debt)
    
    return render_template('all_debts.html', 
                         customers_with_debt=customers_with_debt,
                         total_unpaid=total_unpaid)

# ... (Product routes are unchanged) ...
@app.route('/products')
@login_required
@admin_required
def products():
    products = Product.query.order_by(Product.name).all()
    return render_template('products.html', products=products)

@app.route('/product/add', methods=['POST'])
@login_required
@admin_required
def add_product():
    name = request.form['name']
    stock = int(request.form.get('stock', 0) or 0)
    wholesale = request.form.get('price_wholesale') or '0'
    retail = request.form.get('price_retail') or '0'
    notes = request.form.get('notes')
    if not name:
        flash('اسم المنتج مطلوب')
        return redirect(url_for('products'))
    p = Product(
        name=name,
        stock=stock,
        price_wholesale=Decimal(wholesale),
        price_retail=Decimal(retail),
        notes=notes
    )
    db.session.add(p)
    db.session.commit()
    flash('تمت إضافة المنتج')
    return redirect(url_for('products'))

@app.route('/product/update/<int:id>', methods=['POST'])
@login_required
@admin_required
def update_product(id):
    p = Product.query.get_or_404(id)
    p.name = request.form.get('name') or p.name
    p.stock = int(request.form.get('stock') or p.stock)
    p.price_wholesale = Decimal(request.form.get('price_wholesale') or p.price_wholesale)
    p.price_retail = Decimal(request.form.get('price_retail') or p.price_retail)
    p.notes = request.form.get('notes') or p.notes
    db.session.commit()
    flash('تم التحديث')
    return redirect(url_for('products'))

@app.route('/product/delete/<int:id>')
@login_required
@admin_required
def delete_product(id):
    p = Product.query.get_or_404(id)
    db.session.delete(p)
    db.session.commit()
    flash('تم الحذف')
    return redirect(url_for('products'))

@app.route('/product/unpack', methods=['POST'])
@login_required
@admin_required
def unpack_product():
    source_product_id = request.form.get('source_product_id')
    quantity = int(request.form.get('quantity', 0))
    pieces_per_unit = int(request.form.get('pieces_per_unit', 30))
    new_price_wholesale_str = request.form.get('new_product_price_wholesale')
    new_price_retail_str = request.form.get('new_product_price_retail')

    if not all([source_product_id, quantity > 0, pieces_per_unit > 0, new_price_wholesale_str, new_price_retail_str]):
        flash('بيانات غير كافية لإتمام عملية التفكيك. يرجى ملء جميع الحقول.')
        return redirect(url_for('products'))

    try:
        new_price_wholesale = Decimal(new_price_wholesale_str)
        new_price_retail = Decimal(new_price_retail_str)
    except Exception:
        flash('الأسعار المدخلة غير صالحة.')
        return redirect(url_for('products'))

    source_product = Product.query.get(source_product_id)
    if not source_product:
        flash('منتج المصدر غير موجود.')
        return redirect(url_for('products'))

    if source_product.stock < quantity:
        flash(f'المخزون غير كافٍ. لديك فقط {source_product.stock} من {source_product.name}.')
        return redirect(url_for('products'))

    # Determine target product name by removing "طبق"
    if 'طبق' in source_product.name:
        target_product_name = source_product.name.replace('طبق', '').strip()
    else:
        target_product_name = "بيض" # Fallback for items that are not trays

    target_product = Product.query.filter_by(name=target_product_name).first()
    if not target_product:
        target_product = Product(
            name=target_product_name, stock=0, price_wholesale=new_price_wholesale,
            price_retail=new_price_retail, notes='تم إنشاؤه تلقائياً من عملية تفكيك'
        )
        db.session.add(target_product)
        flash(f'تم إنشاء منتج جديد باسم "{target_product_name}".')
    else:
        # Update prices for existing unpacked product if needed
        target_product.price_wholesale = new_price_wholesale
        target_product.price_retail = new_price_retail

    source_product.stock -= quantity
    unpacked_quantity = quantity * pieces_per_unit
    target_product.stock += unpacked_quantity
    
    db.session.commit()
    flash(f'تم تفكيك {quantity} من "{source_product.name}" بنجاح إلى {unpacked_quantity} قطعة من "{target_product.name}".')
    
    if request.form.get('redirect_to') == 'fast_selling':
        return redirect(url_for('fast_selling'))
    return redirect(url_for('products'))

@app.route('/product/remove_damaged', methods=['POST'])
@login_required
def remove_damaged_product():
    product_id = request.form.get('product_id')
    quantity_str = request.form.get('quantity')

    if not product_id or not quantity_str:
        flash('لم يتم تحديد المنتج أو الكمية.')
        return redirect(url_for('fast_selling'))

    try:
        quantity = int(quantity_str)
        if quantity <= 0:
            flash('الكمية يجب أن تكون أكبر من صفر.')
            return redirect(url_for('fast_selling'))
    except (ValueError, TypeError):
        flash('كمية غير صالحة.')
        return redirect(url_for('fast_selling'))

    prod = Product.query.get(int(product_id))
    if not prod:
        flash('المنتج غير موجود.')
        return redirect(url_for('fast_selling'))

    if prod.stock < quantity:
        flash(f'المخزون غير كافٍ لـ "{prod.name}". المتوفر: {prod.stock}')
        return redirect(url_for('fast_selling'))

    prod.stock -= quantity
    
    # Log the damaged product removal
    damaged_record = DamagedProduct(
        product_id=prod.id,
        quantity=quantity,
        notes=f'إخراج تالف بواسطة {current_user.username}'
    )
    db.session.add(damaged_record)
    
    db.session.commit()
    
    flash(f"تم إخراج {quantity} قطعة تالفة من مخزون {prod.name}.")
    return redirect(url_for('fast_selling'))

# Sales (subtract stock)
@app.route('/sale/new', methods=['GET', 'POST'])
@login_required
def new_sale():
    if request.method == 'GET':
        customers = Customer.query.order_by(Customer.name).all()
        products = Product.query.order_by(Product.name).all()
        return render_template('sale_form.html', customers=customers, products=products)
    
    customer_id = request.form.get('customer_id') or None
    payment_type = request.form.get('payment_type') or 'cash'
    notes = request.form.get('notes')
    paid_amount_str = request.form.get('paid_amount') or '0'
    paid_amount = Decimal(paid_amount_str)

    sale = Sale(
        customer_id=customer_id or None, 
        payment_type=payment_type, 
        notes=notes,
        paid_amount=paid_amount
    )
    db.session.add(sale)
    db.session.flush()
    
    product_ids = request.form.getlist('product_id[]')
    qtys = request.form.getlist('qty[]')
    prices = request.form.getlist('price[]')
    total = Decimal('0')
    
    for pid, q, pr in zip(product_ids, qtys, prices):
        if not pid or int(q) <= 0:
            continue
        prod = Product.query.get(int(pid))
        qty = int(q)
        unit_price = Decimal(pr or '0')
        
        if prod.stock < qty:
            flash(f"المخزون غير كافٍ للمنتج: {prod.name} (المتواجد: {prod.stock})")
            db.session.rollback()
            return redirect(url_for('new_sale'))
        
        itm = SaleItem(
            sale_id=sale.id, product_id=prod.id, qty=qty, 
            unit_price=unit_price, cost_price=prod.price_wholesale
        )
        db.session.add(itm)
        prod.stock -= qty
        total += unit_price * qty
    
    sale.total = total
    sale.due_amount = total - paid_amount

    if paid_amount >= total:
        sale.payment_type = 'cash'
        sale.due_amount = 0
        sale.paid_amount = total
    elif paid_amount > 0 and paid_amount < total:
        sale.payment_type = 'partial'
    else:
        sale.payment_type = 'credit'

    # Add to debt ledger if there's a due amount
    if sale.due_amount > 0 and sale.customer_id:
        debt_entry = DebtTransaction(
            customer_id=sale.customer_id,
            sale_id=sale.id,
            transaction_type='debt',
            amount=sale.due_amount,
            description=f'دين من الفاتورة رقم #{sale.id}'
        )
        db.session.add(debt_entry)

    db.session.commit()
    
    flash('تمت عملية البيع')
    return redirect(url_for('invoice', sale_id=sale.id))

# Invoice (A5) view
@app.route('/invoice/<int:sale_id>')
@login_required
def invoice(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    previous_debt = Decimal('0')
    
    if sale.customer_id:
        customer = Customer.query.get(sale.customer_id)
        current_balance = customer.balance if customer else Decimal('0')
        # Subtract this sale's due amount to get the balance *before* this sale
        previous_debt = current_balance - sale.due_amount

    return render_template('invoice_a5.html', sale=sale, previous_debt=previous_debt)

# Sale receipt (A5 simple)
@app.route('/receipt/<int:sale_id>')
@login_required
def receipt(sale_id):
    sale = Sale.query.get_or_404(sale_id)
    previous_debt = Decimal('0')

    if sale.customer_id:
        customer = Customer.query.get(sale.customer_id)
        current_balance = customer.balance if customer else Decimal('0')
        previous_debt = current_balance - sale.due_amount

    return render_template('receipt_a5.html', sale=sale, previous_debt=previous_debt)

# ... (Inventory and Fast Selling routes are unchanged) ...
@app.route('/inventory')
@login_required
@admin_required
def inventory():
    products = Product.query.order_by(Product.stock.asc()).all()
    return render_template('inventory.html', products=products)

@app.route('/fast_selling')
@login_required
def fast_selling():
    all_products = Product.query.order_by(Product.name).all()
    default_product = next((p for p in all_products if 'بيض' in p.name.lower()), None)
    if not default_product and all_products:
        default_product = all_products[0]
    tray_keywords = ['صغير', 'متوسط', 'خشن', 'كبير']
    tray_products = [p for p in all_products if any(keyword in p.name for keyword in tray_keywords)]
    return render_template('fast_selling.html', 
                           products=all_products,
                           default_product=default_product,
                           tray_products=tray_products)

@app.route('/fast_sell', methods=['POST'])
@login_required
def fast_sell():
    product_id = request.form.get('product_id')
    quantity_str = request.form.get('quantity')
    
    if not product_id or not quantity_str:
        flash('لم يتم تحديد المنتج أو الكمية.')
        return redirect(url_for('fast_selling'))

    try:
        quantity = int(quantity_str)
        if quantity <= 0:
            flash('الكمية يجب أن تكون أكبر من صفر.')
            return redirect(url_for('fast_selling'))
    except (ValueError, TypeError):
        flash('كمية غير صالحة.')
        return redirect(url_for('fast_selling'))

    prod = Product.query.get(int(product_id))
    if not prod:
        flash('المنتج غير موجود.')
        return redirect(url_for('fast_selling'))

    if prod.stock < quantity:
        flash(f'المخزون غير كافٍ لـ "{prod.name}". المتوفر: {prod.stock}')
        return redirect(url_for('fast_selling'))

    prod.stock -= quantity
    
    total = prod.price_retail * quantity
    sale = Sale(payment_type='cash', total=total, paid_amount=total, due_amount=0, notes='بيع سريع')
    db.session.add(sale)
    db.session.flush()
    
    item = SaleItem(
        sale_id=sale.id, 
        product_id=prod.id, 
        qty=quantity, 
        unit_price=prod.price_retail,
        cost_price=prod.price_wholesale 
    )
    db.session.add(item)
    db.session.commit()
    
    flash(f"تم بيع {quantity} من {prod.name} بنجاح.")
    return redirect(url_for('fast_selling'))

# Static download of the SQLite DB for backup
@app.route('/download/db')
@login_required
@admin_required
def download_db():
    return send_from_directory('instance', 'egg_store.db', as_attachment=True)

@app.route('/reports')
@login_required
@admin_required
def reports():
    return render_template('reports.html')

# XLS Export Routes
@app.route('/report/daily_sales/xls')
@login_required
@admin_required
def export_daily_sales_xls():
    today = date.today()
    sales_items = SaleItem.query.join(Sale).filter(func.date(Sale.date) == today).order_by(Sale.date.asc()).all()

    if not sales_items:
        flash('لا توجد مبيعات اليوم لتصديرها.')
        return redirect(url_for('reports'))

    data = []
    for item in sales_items:
        data.append({
            'الوقت': item.sale.date.strftime('%H:%M:%S'),
            'الزبون': item.sale.customer.name if item.sale.customer else 'بيع مباشر',
            'المنتج': item.product.name,
            'الكمية': item.qty,
            'سعر الوحدة': item.unit_price,
            'الإجمالي': item.qty * item.unit_price,
            'نوع الدفع': item.sale.payment_type,
        })
    
    df = pd.DataFrame(data)
    
    # Add a summary row
    total_sales = df['الإجمالي'].sum()
    summary_df = pd.DataFrame([{'الوقت': '', 'الزبون': '', 'المنتج': '', 'الكمية': '', 'سعر الوحدة': 'المجموع الكلي', 'الإجمالي': total_sales, 'نوع الدفع': ''}])
    df = pd.concat([df, summary_df], ignore_index=True)

    output = io.BytesIO()
    df.to_excel(output, index=False, sheet_name='تقرير المبيعات اليومي')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'daily_sales_{today}.xlsx'
    )

@app.route('/report/sales_by_date/xls')
@login_required
@admin_required
def export_sales_by_date_xls():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if not start_date_str or not end_date_str:
        flash('يرجى تحديد تاريخ البدء والانتهاء.')
        return redirect(url_for('reports'))

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('صيغة التاريخ غير صالحة.')
        return redirect(url_for('reports'))

    sales_items = SaleItem.query.join(Sale).filter(
        and_(func.date(Sale.date) >= start_date, func.date(Sale.date) <= end_date)
    ).order_by(Sale.date.asc()).all()

    if not sales_items:
        flash(f'لا توجد مبيعات في الفترة من {start_date_str} إلى {end_date_str}.')
        return redirect(url_for('reports'))

    data = []
    for item in sales_items:
        data.append({
            'التاريخ': item.sale.date.strftime('%Y-%m-%d'),
            'الزبون': item.sale.customer.name if item.sale.customer else 'بيع مباشر',
            'المنتج': item.product.name,
            'الكمية': item.qty,
            'سعر الوحدة': item.unit_price,
            'الإجمالي': item.qty * item.unit_price,
        })

    df = pd.DataFrame(data)
    total_sales = df['الإجمالي'].sum()
    summary_df = pd.DataFrame([{'التاريخ': '', 'الزبون': '', 'المنتج': '', 'الكمية': 'المجموع الكلي', 'سعر الوحدة': '', 'الإجمالي': total_sales}])
    df = pd.concat([df, summary_df], ignore_index=True)

    output = io.BytesIO()
    df.to_excel(output, index=False, sheet_name='تقرير المبيعات')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'sales_{start_date_str}_to_{end_date_str}.xlsx'
    )

@app.route('/report/debts/xls')
@login_required
@admin_required
def export_debts_xls():
    customers_with_debt_query = db.session.query(
        Customer,
        (func.sum(case((DebtTransaction.transaction_type == 'debt', DebtTransaction.amount), else_=0)) -
         func.sum(case((DebtTransaction.transaction_type == 'payment', DebtTransaction.amount), else_=0))).label('total_debt')
    ).join(DebtTransaction).group_by(Customer).order_by(db.text('total_debt DESC')).all()

    customers_with_debt = [c for c in customers_with_debt_query if c.total_debt > 0]

    if not customers_with_debt:
        flash('لا توجد ديون حالياً.')
        return redirect(url_for('all_debts'))

    data = []
    for customer in customers_with_debt:
        data.append({
            'اسم الزبون': customer.Customer.name,
            'رقم الهاتف': customer.Customer.phone,
            'مبلغ الدين': customer.total_debt,
        })
    
    df = pd.DataFrame(data)
    total_debt = df['مبلغ الدين'].sum()
    summary_df = pd.DataFrame([{'اسم الزبون': 'المجموع الكلي للديون', 'رقم الهاتف': '', 'مبلغ الدين': total_debt}])
    df = pd.concat([df, summary_df], ignore_index=True)

    output = io.BytesIO()
    df.to_excel(output, index=False, sheet_name='تقرير الديون')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'debts_report_{date.today()}.xlsx'
    )

@app.route('/report/damaged/xls')
@login_required
@admin_required
def export_damaged_products_xls():
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')

    if not start_date_str or not end_date_str:
        flash('يرجى تحديد تاريخ البدء والانتهاء.')
        return redirect(url_for('reports'))

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        flash('صيغة التاريخ غير صالحة.')
        return redirect(url_for('reports'))

    damaged_products = DamagedProduct.query.filter(
        and_(func.date(DamagedProduct.date) >= start_date, func.date(DamagedProduct.date) <= end_date)
    ).order_by(DamagedProduct.date.asc()).all()

    if not damaged_products:
        flash(f'لا يوجد بيض تالف في الفترة من {start_date_str} إلى {end_date_str}.')
        return redirect(url_for('reports'))

    data = []
    for item in damaged_products:
        data.append({
            'التاريخ': item.date.strftime('%Y-%m-%d'),
            'المنتج': item.product.name,
            'الكمية التالفة': item.quantity,
            'ملاحظات': item.notes,
        })

    df = pd.DataFrame(data)
    total_damaged = df['الكمية التالفة'].sum()
    summary_df = pd.DataFrame([{'التاريخ': '', 'المنتج': 'المجموع الكلي', 'الكمية التالفة': total_damaged, 'ملاحظات': ''}])
    df = pd.concat([df, summary_df], ignore_index=True)

    output = io.BytesIO()
    df.to_excel(output, index=False, sheet_name='تقرير البيض التالف')
    output.seek(0)

    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'damaged_products_{start_date_str}_to_{end_date_str}.xlsx'
    )

def create_default_users():
    if User.query.first() is None:
        admin = User(username='admin', role='admin')
        admin.set_password('admin')
        seller = User(username='seller', role='seller')
        seller.set_password('seller')
        db.session.add(admin)
        db.session.add(seller)
        db.session.commit()
        print("Default users (admin, seller) created.")

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        create_default_users()
    app.run(debug=True, host='0.0.0.0')
