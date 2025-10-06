"""
start_simulation.py

Starts a Flask + Socket.IO server, serves the `templates/simulation_dashboard.html`
and runs the Bookstore simulation in a background thread while emitting
updates to the connected dashboard clients.

Note: requires `flask` and `flask_socketio` installed. Use `pip install flask flask-socketio`.
"""
import threading
import time
import webbrowser
from flask import Flask, render_template
from flask_socketio import SocketIO

from ontology import seed_data, onto
from model import BookstoreModel
from rules import run_reasoner_safely
from bus import TOPIC_PURCHASE_RES, TOPIC_RESTOCK_DONE, TOPIC_RESTOCK_REQ


app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Runtime state
sim = None
sim_thread = None
sim_lock = threading.Lock()
sim_running = False
sim_speed = 1.0  # multiplier, higher is faster (reduces sleep)


def create_simulation(seed=42, steps=30):
    """Create a fresh simulation instance using ontology seed data."""
    inv, books, customers, employees = seed_data()
    m = BookstoreModel(inv, books, customers, employees,
                       restock_threshold=3, restock_amount=4, steps=steps, seed=seed)

    # Initialize server-side logs and transactions storage
    m._server_transactions = []
    m._server_live_logs = []
    m._server_step = 0
    m._restock_costs = 0.0
    m._employee_stats = {}  # Track per-employee statistics

    # Wire model bus to forward events to websocket clients
    def purchase_handler(payload):
        # payload: {status, customer, book}
        try:
            customer_obj = payload.get('customer')
            book_obj = payload.get('book')
            customer_name = getattr(customer_obj, 'name', str(customer_obj))
            # prefer human-friendly title when available
            book_name = getattr(book_obj, 'title', None) or getattr(book_obj, 'name', str(book_obj))
            price = float(getattr(book_obj, 'hasPrice', 0) or 0)

            # record transaction server-side
            tx = {
                'step': int(m._server_step or 0),
                'customer': customer_name,
                'book': book_name,
                'price': price,
                'status': payload.get('status')
            }
            m._server_transactions.append(tx)

            # also add a live log message
            msg = {'message': f"{customer_name} purchased {book_name} for LKR {price:.2f}", 'timestamp': time.time()}
            m._server_live_logs.append(msg)

            socketio.emit('new_transaction', tx)
        except Exception:
            pass

    def restock_handler(payload):
        try:
            book_obj = payload.get('book')
            by_obj = payload.get('by')
            by_name = None
            try:
                by_name = getattr(by_obj, 'title', None) or getattr(by_obj, 'name', None) or (str(by_obj) if by_obj is not None else None)
            except Exception:
                by_name = str(by_obj) if by_obj is not None else None
            if not by_name:
                by_name = 'System'
            
            # compute restock cost (use model's restock_amount and book price)
            book_obj = payload.get('book')
            restock_qty = getattr(m, 'restock_amount', 0) or 0
            # use payload cost if provided (model computes selling_price-200 per book)
            if 'cost' in payload and payload.get('cost') is not None:
                cost = float(payload.get('cost') or 0.0)
            else:
                price = float(getattr(book_obj, 'hasPrice', 0) or 0)
                restock_price_per_book = max(0.0, price - 200.0)
                cost = restock_qty * restock_price_per_book
                
            data = {
                'book': getattr(book_obj, 'title', None) or getattr(book_obj, 'name', str(book_obj)),
                'by': by_name,
                'cost': cost,  # Include cost in the data
                'quantity': restock_qty,  # Include quantity in the data
            }
            # record costs server-side
            m._restock_costs = (getattr(m, '_restock_costs', 0.0) or 0.0) + cost

            # Update employee statistics
            if not hasattr(m, '_employee_stats'):
                m._employee_stats = {}
            if by_name not in m._employee_stats:
                m._employee_stats[by_name] = {'restocks_handled': 0, 'cost_incurred': 0.0}
            m._employee_stats[by_name]['restocks_handled'] += 1
            m._employee_stats[by_name]['cost_incurred'] += cost

            # record restock as log
            # include employee name and step information in the server log message
            current_step = getattr(m, '_server_step', 0) or 0
            m._server_live_logs.append({
                'message': f"Step {current_step}: Restocked {data['book']} by {by_name} (qty={restock_qty}, cost=LKR {cost:.2f})", 
                'timestamp': time.time(),
                'step': current_step
            })
            socketio.emit('restock_event', data)

            # Immediately update clients about revenue/costs
            socketio.emit('revenue_update', {
                'total_revenue': sum(t.get('price', 0) for t in m._server_transactions),
                'total_costs': float(m._restock_costs),
                'net_profit': sum(t.get('price', 0) for t in m._server_transactions) - float(m._restock_costs),
            })
        except Exception:
            pass

    m.bus.subscribe(TOPIC_PURCHASE_RES, purchase_handler)
    m.bus.subscribe(TOPIC_RESTOCK_DONE, restock_handler)
    return m


def snapshot(simulation):
    """Produce a JSON-serializable snapshot of the simulation state."""
    books = []
    for b in simulation.books:
        try:
            books.append({
                'id': getattr(b, 'name', b.name),
                'name': getattr(b, 'title', None) or getattr(b, 'name', b.name),
                'author': getattr(b, 'hasAuthor', '') or '',
                'quantity': int(getattr(b, 'availableQuantity', 0) or 0),
                'price': float(getattr(b, 'hasPrice', 0) or 0),
                'genres': list(getattr(b, 'hasGenre', []) or []),
            })
        except Exception:
            pass

    customers = []
    try:
        for c in onto.Customer.instances():
            purchases = getattr(c, 'purchases', []) or []
            purchase_count = len(purchases)
            total_spent = 0.0
            for bk in purchases:
                try:
                    total_spent += float(getattr(bk, 'hasPrice', 0) or 0)
                except Exception:
                    pass
            customers.append({
                'name': c.name,
                'purchase_count': purchase_count,
                'total_spent': total_spent,
            })
    except Exception:
        pass

    # employees: try to get from simulation if available, otherwise from ontology
    employees = []
    try:
        emps = getattr(simulation, 'employees', None)
        if emps is None:
            emps = onto.Employee.instances()
        employee_stats = getattr(simulation, '_employee_stats', {})
        for e in emps:
            try:
                emp_name = getattr(e, 'name', str(e))
                stats = employee_stats.get(emp_name, {'restocks_handled': 0, 'cost_incurred': 0.0})
                employees.append({
                    'name': emp_name,
                    'restocks_handled': stats['restocks_handled'],
                    'cost_incurred': stats['cost_incurred'],
                })
            except Exception:
                pass
    except Exception:
        employees = []

    # compute simple stats from recorded transactions
    transactions = list(getattr(simulation, '_server_transactions', []))
    total_revenue = sum(t.get('price', 0) for t in transactions)
    total_restock_costs = float(getattr(simulation, '_restock_costs', 0.0) or 0.0)
    net_profit = total_revenue - total_restock_costs

    data = {
        'books': books,
        'customers': customers,
        'employees': employees,
        'transactions': transactions,
        'live_log_messages': list(getattr(simulation, '_server_live_logs', [])),
        'current_step': int(getattr(simulation, '_server_step', 0) or 0),
        'max_steps': int(getattr(simulation, 'steps', 0) or 0),
        'stats': {
            'total_transactions': int(getattr(simulation, 'total_transactions', 0) or 0),
            'total_restocks': int(getattr(simulation, 'restock_events', 0) or 0),
            'total_revenue': float(total_revenue),
            'total_restocking_cost': float(total_restock_costs),
            'net_profit': float(net_profit),
        }
    }
    return data


def simulation_runner(simulation):
    global sim_running
    base_sleep = 0.7
    sim_running = True

    # Emit initial snapshot
    socketio.emit('simulation_update', snapshot(simulation))

    try:
        for i in range(simulation.steps):
            if not sim_running:
                break
            simulation.step()
            # update server-side step counter
            simulation._server_step = i + 1
            # emit periodic revenue update
            socketio.emit('revenue_update', {
                'total_revenue': sum(t.get('price', 0) for t in simulation._server_transactions),
                'total_costs': 0.0,
                'net_profit': sum(t.get('price', 0) for t in simulation._server_transactions),
            })
            # emit log messages batch
            socketio.emit('log_message', {'messages': list(simulation._server_live_logs)})
            # Run reasoner occasionally (handled inside model.run but when stepping manually we call periodically)
            if (i + 1) % 5 == 0:
                run_reasoner_safely()

            # Send snapshot to clients
            socketio.emit('simulation_update', snapshot(simulation))

            # small sleep controlled by speed multiplier
            sleep_time = max(0.01, base_sleep / max(0.01, sim_speed))
            time.sleep(sleep_time)

        # final reasoner pass
        run_reasoner_safely()
        socketio.emit('simulation_finished', {'total_transactions': simulation.total_transactions})
    finally:
        sim_running = False


@app.route('/')
def index():
    return render_template('simulation_dashboard.html')


@app.route('/api/data')
def api_data():
    with sim_lock:
        if sim:
            return socketio.server.eio.encode(socketio.server, snapshot(sim)) if False else (snapshot(sim))
        else:
            return snapshot(create_simulation())


@socketio.on('connect')
def on_connect():
    # send current snapshot immediately
    with sim_lock:
        if sim:
            socketio.emit('simulation_update', snapshot(sim))


@socketio.on('start_simulation')
def on_start(data=None):
    global sim, sim_thread, sim_running
    with sim_lock:
        if sim_running:
            return
        
        # Get steps from client data, default to 30 if not provided
        steps = 30
        if data and isinstance(data, dict):
            steps = int(data.get('steps', 30))
        
        # Ensure steps is within valid range (10-100)
        steps = max(10, min(100, steps))
        
        # Create new simulation with specified steps
        sim = create_simulation(steps=steps)
        sim_thread = threading.Thread(target=simulation_runner, args=(sim,), daemon=True)
        sim_thread.start()


@socketio.on('stop_simulation')
def on_stop(data=None):
    global sim_running
    sim_running = False
    socketio.emit('simulation_update', {'status': 'stopped'})


@socketio.on('reset_simulation')
def on_reset(data=None):
    global sim, sim_thread, sim_running
    sim_running = False
    # small wait to ensure runner exits
    time.sleep(0.1)
    with sim_lock:
        sim = create_simulation(steps=30)  # Reset with default 30 steps
    socketio.emit('simulation_reset', snapshot(sim))


@socketio.on('set_speed')
def on_set_speed(data):
    global sim_speed
    try:
        speed = float(data.get('speed', 1.0))
        sim_speed = max(0.01, speed)
    except Exception:
        pass


@socketio.on('add_employee')
def on_add_employee(data):
    """Create an Employee in the ontology and register an agent at runtime.
    Expects data: { 'name': 'Emp_Name' }"""
    name = (data or {}).get('name', '').strip()
    
    if not name:
        socketio.emit('notification', {'message': 'Employee name is required', 'type': 'error'})
        return {'status': 'error', 'error': 'Name required'}
    
    with sim_lock:
        try:
            # Check if employee name already exists
            existing_employees = [e.name for e in onto.Employee.instances()]
            if name in existing_employees:
                socketio.emit('notification', {'message': f'Employee {name} already exists', 'type': 'error'})
                return {'status': 'error', 'error': 'Employee already exists'}
            
            # Create ontology Employee with given name
            from ontology import Employee
            new_emp = Employee(name)
            
            # Add to simulation if it exists
            if sim:
                sim.add_employee(new_emp)
                # Return updated snapshot
                socketio.emit('simulation_update', snapshot(sim))
                socketio.emit('notification', {'message': f'Employee {name} added successfully', 'type': 'success'})
                return {'status': 'ok', 'name': name}
            else:
                socketio.emit('notification', {'message': 'No simulation running', 'type': 'error'})
                return {'status': 'no_sim'}
        except Exception as e:
            socketio.emit('notification', {'message': f'Error adding employee: {str(e)}', 'type': 'error'})
            return {'status': 'error', 'error': str(e)}


@socketio.on('remove_employee')
def on_remove_employee(data):
    """Remove an Employee from the ontology and simulation.
    Expects data: { 'name': 'Employee_Name' }"""
    name = (data or {}).get('name', '').strip()
    
    if not name:
        socketio.emit('notification', {'message': 'Employee name is required', 'type': 'error'})
        return {'status': 'error', 'error': 'Name required'}
    
    with sim_lock:
        try:
            # Find the employee in ontology
            employee_to_remove = None
            for employee in onto.Employee.instances():
                if employee.name == name:
                    employee_to_remove = employee
                    break
            
            if not employee_to_remove:
                socketio.emit('notification', {'message': f'Employee {name} not found', 'type': 'error'})
                return {'status': 'error', 'error': 'Employee not found'}
            
            # Remove from simulation if it exists
            if sim:
                sim.remove_employee(employee_to_remove)
                # Return updated snapshot
                socketio.emit('simulation_update', snapshot(sim))
                socketio.emit('notification', {'message': f'Employee {name} removed successfully', 'type': 'success'})
                return {'status': 'ok', 'name': name}
            else:
                socketio.emit('notification', {'message': 'No simulation running', 'type': 'error'})
                return {'status': 'no_sim'}
        except Exception as e:
            socketio.emit('notification', {'message': f'Error removing employee: {str(e)}', 'type': 'error'})
            return {'status': 'error', 'error': str(e)}


@socketio.on('add_customer')
def on_add_customer(data):
    """Create a Customer in the ontology and register an agent at runtime.
    Expects data: { 'name': 'Customer_Name' }"""
    name = (data or {}).get('name', '').strip()
    
    if not name:
        socketio.emit('notification', {'message': 'Customer name is required', 'type': 'error'})
        return {'status': 'error', 'error': 'Name required'}
    
    with sim_lock:
        try:
            # Check if customer name already exists
            existing_customers = [c.name for c in onto.Customer.instances()]
            if name in existing_customers:
                socketio.emit('notification', {'message': f'Customer {name} already exists', 'type': 'error'})
                return {'status': 'error', 'error': 'Customer already exists'}
            
            # Create ontology Customer with given name
            from ontology import Customer
            new_customer = Customer(name)
            
            # Add to simulation if it exists
            if sim:
                sim.add_customer(new_customer)
                # Return updated snapshot
                socketio.emit('simulation_update', snapshot(sim))
                socketio.emit('notification', {'message': f'Customer {name} added successfully', 'type': 'success'})
                return {'status': 'ok', 'name': name}
            else:
                socketio.emit('notification', {'message': 'No simulation running', 'type': 'error'})
                return {'status': 'no_sim'}
        except Exception as e:
            socketio.emit('notification', {'message': f'Error adding customer: {str(e)}', 'type': 'error'})
            return {'status': 'error', 'error': str(e)}


@socketio.on('remove_customer')
def on_remove_customer(data):
    """Remove a Customer from the ontology and simulation.
    Expects data: { 'name': 'Customer_Name' }"""
    name = (data or {}).get('name', '').strip()
    
    if not name:
        socketio.emit('notification', {'message': 'Customer name is required', 'type': 'error'})
        return {'status': 'error', 'error': 'Name required'}
    
    with sim_lock:
        try:
            # Find the customer in ontology
            customer_to_remove = None
            for customer in onto.Customer.instances():
                if customer.name == name:
                    customer_to_remove = customer
                    break
            
            if not customer_to_remove:
                socketio.emit('notification', {'message': f'Customer {name} not found', 'type': 'error'})
                return {'status': 'error', 'error': 'Customer not found'}
            
            # Remove from simulation if it exists
            if sim:
                sim.remove_customer(customer_to_remove)
                # Return updated snapshot
                socketio.emit('simulation_update', snapshot(sim))
                socketio.emit('notification', {'message': f'Customer {name} removed successfully', 'type': 'success'})
                return {'status': 'ok', 'name': name}
            else:
                socketio.emit('notification', {'message': 'No simulation running', 'type': 'error'})
                return {'status': 'no_sim'}
        except Exception as e:
            socketio.emit('notification', {'message': f'Error removing customer: {str(e)}', 'type': 'error'})
            return {'status': 'error', 'error': str(e)}


@socketio.on('update_stock_level')
def on_update_stock_level(data):
    """Update the maximum stock level for inventory management.
    Expects data: { 'stock_level': int }"""
    stock_level = (data or {}).get('stock_level', 10)
    
    try:
        stock_level = int(stock_level)
        if stock_level < 1 or stock_level > 50:
            socketio.emit('notification', {'message': 'Stock level must be between 1 and 50', 'type': 'error'})
            return {'status': 'error', 'error': 'Invalid range'}
    except ValueError:
        socketio.emit('notification', {'message': 'Invalid stock level value', 'type': 'error'})
        return {'status': 'error', 'error': 'Invalid value'}
    
    with sim_lock:
        try:
            if sim:
                # Update the simulation's max stock level
                sim.max_stock_level = stock_level
                
                # Trigger intelligent restocking for books that need it based on sales velocity
                for book in sim.books:
                    current_qty = book.availableQuantity if book.availableQuantity is not None else 0
                    book_id = book.name
                    
                    # Get sales velocity data
                    total_sales = sim.book_sales_count.get(book_id, 0)
                    recent_sales = len(sim.book_sales_recent.get(book_id, []))
                    steps_elapsed = max(1, sim.current_step)
                    recent_velocity = recent_sales / min(5, max(1, sim.current_step))
                    
                    # Determine if restocking is needed based on velocity and new max level
                    if recent_velocity >= 0.5 and current_qty < stock_level * 0.4:  # Active books need more stock
                        sim.bus.publish(TOPIC_RESTOCK_REQ, {"book": book})
                    elif recent_velocity >= 0.2 and current_qty < stock_level * 0.2:  # Medium activity books
                        sim.bus.publish(TOPIC_RESTOCK_REQ, {"book": book})
                    elif current_qty < stock_level * 0.1:  # Even slow books need minimum stock
                        sim.bus.publish(TOPIC_RESTOCK_REQ, {"book": book})
                
                # Return updated snapshot
                socketio.emit('simulation_update', snapshot(sim))
                return {'status': 'ok', 'stock_level': stock_level}
            else:
                socketio.emit('notification', {'message': 'No simulation running', 'type': 'error'})
                return {'status': 'no_sim'}
        except Exception as e:
            socketio.emit('notification', {'message': f'Error updating stock level: {str(e)}', 'type': 'error'})
            return {'status': 'error', 'error': str(e)}


def open_browser_delayed(url, delay=1.0):
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


if __name__ == '__main__':
    # Prepare a simulation instance so clients see an initial state
    sim = create_simulation(steps=30)  # Initialize with default 30 steps

    # Open browser after server starts
    open_browser_delayed('http://127.0.0.1:5000', delay=1.0)

    # Use socketio.run (will block)
    socketio.run(app, host='0.0.0.0', port=5000)
