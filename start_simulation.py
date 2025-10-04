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
from bus import TOPIC_PURCHASE_RES, TOPIC_RESTOCK_DONE


app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Runtime state
sim = None
sim_thread = None
sim_lock = threading.Lock()
sim_running = False
sim_speed = 1.0  # multiplier, higher is faster (reduces sleep)


def create_simulation(seed=42, steps=100):
    """Create a fresh simulation instance using ontology seed data."""
    inv, books, customers, employees = seed_data()
    m = BookstoreModel(inv, books, customers, employees,
                       restock_threshold=3, restock_amount=4, steps=steps, seed=seed)

    # Initialize server-side logs and transactions storage
    m._server_transactions = []
    m._server_live_logs = []
    m._server_step = 0
    m._restock_costs = 0.0

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
            data = {
                'book': getattr(payload.get('book'), 'title', None) or getattr(payload.get('book'), 'name', str(payload.get('book'))),
                'by': getattr(payload.get('by'), 'name', str(payload.get('by'))),
            }
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
            # record costs server-side
            m._restock_costs = (getattr(m, '_restock_costs', 0.0) or 0.0) + cost

            # record restock as log
            m._server_live_logs.append({'message': f"Restocked {data['book']} by {data['by']} (qty={restock_qty}, cost=LKR {cost:.2f})", 'timestamp': time.time()})
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

    # compute simple stats from recorded transactions
    transactions = list(getattr(simulation, '_server_transactions', []))
    total_revenue = sum(t.get('price', 0) for t in transactions)
    total_restock_costs = float(getattr(simulation, '_restock_costs', 0.0) or 0.0)
    net_profit = total_revenue - total_restock_costs

    data = {
        'books': books,
        'customers': customers,
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
        if sim is None:
            sim = create_simulation()
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
        sim = create_simulation()
    socketio.emit('simulation_reset', snapshot(sim))


@socketio.on('set_speed')
def on_set_speed(data):
    global sim_speed
    try:
        speed = float(data.get('speed', 1.0))
        sim_speed = max(0.01, speed)
    except Exception:
        pass


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
    sim = create_simulation()

    # Open browser after server starts
    open_browser_delayed('http://127.0.0.1:5000', delay=1.0)

    # Use socketio.run (will block)
    socketio.run(app, host='0.0.0.0', port=5000)
