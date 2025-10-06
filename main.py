# main.py

from ontology import onto, seed_data
from rules import run_reasoner_safely
from model import BookstoreModel
from bus import TOPIC_PURCHASE_REQ, TOPIC_PURCHASE_RES, TOPIC_RESTOCK_REQ, TOPIC_RESTOCK_DONE
import time

def setup_terminal_logging(simulation):
    """Set up real-time terminal logging for transactions and restocks"""
    
    def log_purchase_request(payload):
        customer = payload["customer"]
        book = payload["book"]
        customer_name = getattr(customer, 'name', str(customer))
        book_title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
        qty = getattr(book, 'availableQuantity', 0) or 0
        print(f"[Step {simulation.current_step:2d}] 🛍️  {customer_name} wants to buy '{book_title}' (Stock: {qty})")
    
    def log_purchase_result(payload):
        customer = payload["customer"]
        book = payload["book"]
        status = payload["status"]
        customer_name = getattr(customer, 'name', str(customer))
        book_title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
        price = getattr(book, 'hasPrice', 0) or 0
        new_qty = getattr(book, 'availableQuantity', 0) or 0
        
        if status == "ok":
            print(f"[Step {simulation.current_step:2d}] ✅ {customer_name} purchased '{book_title}' for LKR {price:.2f} (Remaining: {new_qty})")
        else:
            print(f"[Step {simulation.current_step:2d}] ❌ {customer_name} couldn't buy '{book_title}' - {status}")
    
    def log_restock_request(payload):
        book = payload["book"]
        book_title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
        qty = getattr(book, 'availableQuantity', 0) or 0
        print(f"[Step {simulation.current_step:2d}] 📦 Restock needed for '{book_title}' (Current stock: {qty})")
    
    def log_restock_done(payload):
        book = payload["book"]
        employee = payload.get("by")
        cost = payload.get("cost", 0)
        book_title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
        new_qty = getattr(book, 'availableQuantity', 0) or 0
        emp_name = getattr(employee, 'name', str(employee)) if employee else "System"
        print(f"[Step {simulation.current_step:2d}] 📈 {emp_name} restocked '{book_title}' for LKR {cost:.2f} (New stock: {new_qty})")
    
    # Subscribe to all transaction events
    
    simulation.bus.subscribe(TOPIC_PURCHASE_REQ, log_purchase_request)
    simulation.bus.subscribe(TOPIC_PURCHASE_RES, log_purchase_result)
    simulation.bus.subscribe(TOPIC_RESTOCK_REQ, log_restock_request)
    simulation.bus.subscribe(TOPIC_RESTOCK_DONE, log_restock_done)

if __name__ == "__main__":
    print("\n" + "="*80)
    print("🏪 BOOKSTORE SIMULATION STARTING")
    print("="*80)
    
    # Initialize simulation
    inv, books, customers, employees = seed_data()
    sim = BookstoreModel(inv, books, customers, employees,
                         restock_threshold=3, restock_amount=4, steps=30, seed=42)
    
    # Set up terminal logging
    setup_terminal_logging(sim)
    
    # Display initial state
    print("\n📚 INITIAL INVENTORY:")
    for book in books:
        title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
        author = getattr(book, 'hasAuthor', 'Unknown')
        price = getattr(book, 'hasPrice', 0) or 0
        qty = getattr(book, 'availableQuantity', 0) or 0
        print(f"  📖 {title} by {author} - LKR {price:.2f} (Stock: {qty})")
    
    print("\n👥 CUSTOMERS:")
    for customer in customers:
        print(f"  🧑 {customer.name}")
    
    print("\n👨‍💼 EMPLOYEES:")
    for employee in employees:
        print(f"  👔 {employee.name}")
    
    print("\n" + "="*80)
    print("🚀 SIMULATION RUNNING (30 steps)...")
    print("="*80)
    
    # Run simulation with real-time logging
    start_time = time.time()
    sim.run()
    end_time = time.time()
    
    run_reasoner_safely()  # final pass
    
    # Calculate financial metrics
    total_revenue = 0.0
    total_costs = 0.0
    
    # Calculate revenue from transactions
    for customer in onto.Customer.instances():
        purchases = getattr(customer, 'purchases', []) or []
        for book in purchases:
            price = getattr(book, 'hasPrice', 0) or 0
            total_revenue += price
    
    # Estimate costs (simplified)
    for book in books:
        initial_qty = 6  # approximate initial stock
        current_qty = getattr(book, 'availableQuantity', 0) or 0
        sold_qty = max(0, initial_qty - current_qty + (sim.restock_events * 2))  # rough estimate
        price = getattr(book, 'hasPrice', 0) or 0
        cost_per_book = max(0, price - 200)  # cost = selling price - 200
        total_costs += cost_per_book * (sim.restock_events * 2)  # rough restock costs
    
    print("\n" + "="*80)
    print("📊 SIMULATION SUMMARY")
    print("="*80)
    print(f"⏱️  Simulation Time: {end_time - start_time:.2f} seconds")
    print(f"🔄 Steps Completed: {sim.current_step}")
    print(f"💰 Total Transactions: {sim.total_transactions}")
    print(f"📦 Restock Events: {sim.restock_events}")
    print(f"💵 Total Revenue: LKR {total_revenue:.2f}")
    print(f"💸 Estimated Costs: LKR {total_costs:.2f}")
    print(f"📈 Net Profit: LKR {total_revenue - total_costs:.2f}")

    print("\n📦 FINAL INVENTORY STATUS:")
    for book in books:
        title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
        author = getattr(book, 'hasAuthor', 'Unknown')
        qty = getattr(book, 'availableQuantity', 0) or 0
        price = getattr(book, 'hasPrice', 0) or 0
        genres = list(getattr(book, 'hasGenre', []) or [])
        status = "🔴 LOW STOCK" if qty < 3 else "🟢 ADEQUATE" if qty < 6 else "🔵 HIGH STOCK"
        print(f"  📖 {title:<20} | Stock: {qty:2d} | LKR {price:7.2f} | {status}")
        print(f"      by {author} | Genres: {', '.join(genres) if genres else 'None'}")

    print("\n🛍️ CUSTOMER PURCHASE HISTORY:")
    for customer in onto.Customer.instances():
        purchases = getattr(customer, 'purchases', []) or []
        if purchases:
            bought_titles = []
            total_spent = 0.0
            for book in purchases:
                title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
                price = getattr(book, 'hasPrice', 0) or 0
                bought_titles.append(f"'{title}' (LKR {price:.2f})")
                total_spent += price
            print(f"  🧑 {customer.name}: {len(purchases)} books, LKR {total_spent:.2f}")
            for title in bought_titles:
                print(f"      └─ {title}")
        else:
            print(f"  🧑 {customer.name}: No purchases")

    # Sales velocity analysis
    print("\n📊 SALES VELOCITY ANALYSIS:")
    for book in books:
        book_id = book.name
        total_sales = sim.book_sales_count.get(book_id, 0)
        velocity = total_sales / max(1, sim.current_step)
        title = getattr(book, 'title', None) or getattr(book, 'name', str(book))
        velocity_status = "🔥 HOT" if velocity >= 0.5 else "📈 WARM" if velocity >= 0.2 else "❄️ COLD"
        print(f"  📖 {title:<20} | Sales: {total_sales:2d} | Velocity: {velocity:.2f}/step | {velocity_status}")

    # Low stock warnings
    low = list(onto.LowStock.instances())
    if low:
        print("\n⚠️ LOW STOCK ALERTS:")
        for stock_alert in low:
            print(f"  🔴 {stock_alert.name}")
    else:
        print("\n✅ NO LOW STOCK ALERTS")

    # Save ontology
    try:
        onto.save(file="bookstore.owl", format="rdfxml")
        print("\n💾 Ontology saved to 'bookstore.owl'")
    except Exception as e:
        print(f"\n❌ Save failed: {e}")
    
    print("\n" + "="*80)
    print("🏁 SIMULATION COMPLETED SUCCESSFULLY!")
    print("="*80)
