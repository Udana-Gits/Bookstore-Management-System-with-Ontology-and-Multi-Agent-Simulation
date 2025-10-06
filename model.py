# model.py
from mesa import Model, Agent
from ontology import onto, Transaction
from rules import run_reasoner_safely, LOW_STOCK_THRESHOLD
from bus import (
    MessageBus,
    TOPIC_PURCHASE_REQ,
    TOPIC_PURCHASE_RES,
    TOPIC_RESTOCK_REQ,
    TOPIC_RESTOCK_DONE,
)


class CustomerAgent(Agent):
    def __init__(self, model, customer_ind):  
        super().__init__(model)
        self.ind = customer_ind

        import random

        customer_types = [
            {"type": "frequent", "probability": 0.7, "patience": 0.9},      # High activity
            {"type": "regular", "probability": 0.4, "patience": 0.7},       # Medium activity  
            {"type": "occasional", "probability": 0.2, "patience": 0.5},    # Low activity
            {"type": "browser", "probability": 0.1, "patience": 0.3}        # Very low activity
        ]

        # Randomly assign customer type with weighted distribution
        weights = [0.2, 0.4, 0.3, 0.1]  
        self.behavior = random.choices(customer_types, weights=weights)[0]
        
    def step(self):
        import random

        if random.random() < self.behavior["probability"]:

            if random.random() < 0.8: 
                book = random.choice(self.model.books)
            else:  
                return
            self.model.bus.publish(TOPIC_PURCHASE_REQ, {"customer": self.ind, "book": book})

class EmployeeAgent(Agent):
    def __init__(self, model, employee_ind):
        super().__init__(model)
        self.ind = employee_ind
        
        import random
        
        work_types = [
            {"type": "diligent", "proactive_probability": 0.6, "efficiency": 0.9},     
            {"type": "regular", "proactive_probability": 0.3, "efficiency": 0.7},        
            {"type": "lazy", "proactive_probability": 0.1, "efficiency": 0.5},        
        ]
        
        weights = [0.3, 0.5, 0.2]  
        self.work_behavior = random.choices(work_types, weights=weights)[0]
        
    def _restock(self, payload):
        book = payload["book"]
        q = book.availableQuantity if book.availableQuantity is not None else 0
        # Apply efficiency to restock amount
        base_restock = self.model.restock_amount
        actual_restock = max(1, int(base_restock * self.work_behavior["efficiency"]))
        book.availableQuantity = q + actual_restock
        
        # compute restock cost: restock price per book = selling price - 200
        try:
            selling_price = float(getattr(book, 'hasPrice', 0) or 0)
        except Exception:
            selling_price = 0.0

        restock_price_per_book = max(0.0, selling_price - 200.0)
        cost = actual_restock * restock_price_per_book
        
        self.model.bus.publish(TOPIC_RESTOCK_DONE, {"book": book, "by": self.ind, "cost": cost})
        
    def step(self):
        # Proactive restocking behavior - employees may check inventory and restock low books
        import random
        if random.random() < self.work_behavior["proactive_probability"]:
            # Check for books that need restocking
            low_stock_books = [book for book in self.model.books 
                             if (book.availableQuantity or 0) < self.model.restock_threshold]
            if low_stock_books:
                # Pick a random low stock book to restock
                book_to_restock = random.choice(low_stock_books)
                # Only restock if no one else is already handling it this step
                if random.random() < 0.7:  # 70% chance to actually do the work
                    self.model.bus.publish(TOPIC_RESTOCK_REQ, {"book": book_to_restock})

class BookstoreModel(Model):
    def __init__(self, inv, books, customers, employees,
                 restock_threshold=LOW_STOCK_THRESHOLD, restock_amount=4, steps=30, seed=None):
        super().__init__(seed=seed)
        self.bus = MessageBus()
        self.inventory, self.books = inv, books
        self.customers, self.employees = customers, employees
        self.restock_threshold, self.restock_amount = restock_threshold, restock_amount
        self.max_stock_level = 10  # Default maximum stock level
        self.steps = steps
        self.total_transactions = 0
        self.restock_events = 0
        
        # Track sales velocity for intelligent restocking
        self.book_sales_count = {}  # Track total sales per book
        self.book_sales_recent = {}  # Track recent sales (last 5 steps) per book
        self.current_step = 0
        
        # Initialize sales tracking for each book
        for book in self.books:
            book_id = book.name
            self.book_sales_count[book_id] = 0
            self.book_sales_recent[book_id] = []
        # subscribe model handlers
        self.bus.subscribe(TOPIC_PURCHASE_REQ, self._handle_purchase)
        # handle restock requests centrally so only one employee performs the restock
        self.bus.subscribe(TOPIC_RESTOCK_REQ, self._perform_restock)
        self.bus.subscribe(TOPIC_RESTOCK_DONE, lambda _: self._count_restock())

        # create agents (auto-registered in Mesa 3.x)
        for c in self.customers:
            CustomerAgent(self, c)
        for e in self.employees:
            EmployeeAgent(self, e)

    def _count_restock(self): self.restock_events += 1

    def _perform_restock(self, payload):
        """Handle a restock request with intelligent restocking based on sales velocity.
        Publishes TOPIC_RESTOCK_DONE with payload {'book': book, 'by': employee, 'cost': cost}.
        """
        book = payload.get('book')
        if book is None:
            return

        # pick an employee in round-robin fashion
        if not self.employees:
            chosen = None
        else:
            if not hasattr(self, '_next_employee_index'):
                self._next_employee_index = 0
            chosen = self.employees[self._next_employee_index % len(self.employees)]
            self._next_employee_index = (self._next_employee_index + 1) % max(1, len(self.employees))

        # Calculate intelligent restock amount based on sales velocity
        book_id = book.name
        current_qty = book.availableQuantity if book.availableQuantity is not None else 0
        
        # Get sales data
        total_sales = self.book_sales_count.get(book_id, 0)
        recent_sales = len(self.book_sales_recent.get(book_id, []))
        
        
        steps_elapsed = max(1, self.current_step)
        sales_velocity = total_sales / steps_elapsed
        recent_velocity = recent_sales / min(5, max(1, self.current_step))  
        
        # Determine target stock level based on sales velocity
        if recent_velocity >= 1.5:  # Very fast selling (>1.5 sales per step)
            target_stock = min(self.max_stock_level, 25)  # High stock for fast movers
        elif recent_velocity >= 1.0:  # Fast selling (1+ sales per step)
            target_stock = min(self.max_stock_level, 15)  # Medium-high stock
        elif recent_velocity >= 0.5:  # Medium selling (0.5+ sales per step)
            target_stock = min(self.max_stock_level, 8)   # Medium stock
        elif recent_velocity >= 0.2:  # Slow selling (0.2+ sales per step)
            target_stock = min(self.max_stock_level, 5)   # Low stock
        else:  # Very slow selling (<0.2 sales per step)
            target_stock = min(self.max_stock_level, 2)   # Minimal stock
        
        # Calculate restock amount (max 6 per restock as requested)
        needed_stock = max(0, target_stock - current_qty)
        restock_qty = min(6, needed_stock)  # Maximum 6 books per restock
        
        # Respect max stock level - don't exceed it
        if current_qty + restock_qty > self.max_stock_level:
            restock_qty = max(0, self.max_stock_level - current_qty)
        
        # Only proceed if there's something to restock
        if restock_qty > 0:
            book.availableQuantity = current_qty + restock_qty
        else:
            restock_qty = 0  # No restocking needed

        # compute restock cost with formula: restock_price_per_book = selling_price - 200
        try:
            selling_price = float(getattr(book, 'hasPrice', 0) or 0)
        except Exception:
            selling_price = 0.0
        restock_price_per_book = max(0.0, selling_price - 200.0)
        cost = restock_qty * restock_price_per_book

        # publish restock done
        self.bus.publish(TOPIC_RESTOCK_DONE, {"book": book, "by": chosen, "cost": cost})

    def _handle_purchase(self, payload):
        cust, book = payload["customer"], payload["book"]
        q = book.availableQuantity if book.availableQuantity is not None else 0
        if q <= 0:
            self.bus.publish(TOPIC_PURCHASE_RES, {"status": "out_of_stock", "customer": cust, "book": book})
            return
        # decrement
        book.availableQuantity = q - 1
        
        # Track sales for intelligent restocking
        book_id = book.name
        if book_id not in self.book_sales_count:
            self.book_sales_count[book_id] = 0
            self.book_sales_recent[book_id] = []
        
        self.book_sales_count[book_id] += 1
        self.book_sales_recent[book_id].append(self.current_step)
        
        # Keep only recent sales (last 5 steps)
        recent_threshold = max(0, self.current_step - 5)
        self.book_sales_recent[book_id] = [step for step in self.book_sales_recent[book_id] if step > recent_threshold]
        
        # record transaction (+ SWRL may infer purchases)
        with onto:
            t = Transaction(f"T_{cust.name}_{book.name}_{self.total_transactions}")
            t.involves = [cust, book]
            cust.purchases.append(book)  # explicit assertion (works even without reasoner)
        self.total_transactions += 1
        self.bus.publish(TOPIC_PURCHASE_RES, {"status": "ok", "customer": cust, "book": book})
        
        # trigger intelligent restock if needed
        new_qty = book.availableQuantity if book.availableQuantity is not None else 0
        if new_qty < self.restock_threshold:
            self.bus.publish(TOPIC_RESTOCK_REQ, {"book": book})

    def step(self):
        # Increment step counter for sales velocity tracking
        self.current_step += 1
        # Random activation equivalent in Mesa 3.x
        self.agents.shuffle_do("step")

    def run(self):
        for i in range(self.steps):
            self.step()
            if (i+1) % 5 == 0:
                run_reasoner_safely()  # refresh SWRL inferences periodically

    def add_employee(self, employee_entity):
        """Register a new Employee ontology entity at runtime and create an agent for it."""
        # attach to inventory
        try:
            employee_entity.worksAt = [self.inventory]
        except Exception:
            pass
        # create agent (auto registers)
        EmployeeAgent(self, employee_entity)
        # also add to model.employees list
        try:
            self.employees.append(employee_entity)
        except Exception:
            pass

    def add_customer(self, customer_entity):
        """Register a new Customer ontology entity at runtime and create an agent for it."""
        # create agent (auto registers)
        CustomerAgent(self, customer_entity)
        # also add to model.customers list
        try:
            self.customers.append(customer_entity)
        except Exception:
            pass

    def remove_customer(self, customer_entity):
        """Remove a Customer ontology entity and its agent from the simulation."""
        # Remove from customers list
        try:
            if customer_entity in self.customers:
                self.customers.remove(customer_entity)
        except Exception:
            pass
        
        # Remove agent from Mesa's agent list
        try:
            agents_to_remove = []
            for agent in self.agents:
                if hasattr(agent, 'ind') and agent.ind == customer_entity:
                    agents_to_remove.append(agent)
            
            for agent in agents_to_remove:
                self.agents.remove(agent)
        except Exception:
            pass
        
        # Remove from ontology
        try:
            from owlready2 import destroy_entity
            destroy_entity(customer_entity)
        except Exception:
            pass

    def remove_employee(self, employee_entity):
        """Remove an Employee ontology entity and its agent from the simulation."""
        # Remove from employees list
        try:
            if employee_entity in self.employees:
                self.employees.remove(employee_entity)
        except Exception:
            pass
        
        # Remove agent from Mesa's agent list
        try:
            agents_to_remove = []
            for agent in self.agents:
                if hasattr(agent, 'ind') and agent.ind == employee_entity:
                    agents_to_remove.append(agent)
            
            for agent in agents_to_remove:
                self.agents.remove(agent)
        except Exception:
            pass
        
        # Remove from ontology
        try:
            from owlready2 import destroy_entity
            destroy_entity(employee_entity)
        except Exception:
            pass
