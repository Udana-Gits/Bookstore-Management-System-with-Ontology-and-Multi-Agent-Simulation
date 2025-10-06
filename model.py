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
    def __init__(self, model, customer_ind):  # Mesa 3.x: no unique_id arg
        super().__init__(model)
        self.ind = customer_ind
    def step(self):
        import random
        book = random.choice(self.model.books)
        self.model.bus.publish(TOPIC_PURCHASE_REQ, {"customer": self.ind, "book": book})

class EmployeeAgent(Agent):
    def __init__(self, model, employee_ind):
        super().__init__(model)
        self.ind = employee_ind
        # Employees don't subscribe directly to restock requests anymore.
        # The model will centrally assign restock requests to a single employee.
    def _restock(self, payload):
        book = payload["book"]
        q = book.availableQuantity if book.availableQuantity is not None else 0
        book.availableQuantity = q + self.model.restock_amount
        # compute restock cost: restock price per book = selling price - 200
        try:
            selling_price = float(getattr(book, 'hasPrice', 0) or 0)
        except Exception:
            selling_price = 0.0
        restock_price_per_book = max(0.0, selling_price - 200.0)
        restock_qty = getattr(self.model, 'restock_amount', 0) or 0
        cost = restock_qty * restock_price_per_book
        # publish restock done with computed cost
        self.model.bus.publish(TOPIC_RESTOCK_DONE, {"book": book, "by": self.ind, "cost": cost})
    def step(self): pass  # (optional proactive restock)

class BookstoreModel(Model):
    def __init__(self, inv, books, customers, employees,
                 restock_threshold=LOW_STOCK_THRESHOLD, restock_amount=4, steps=30, seed=None):
        super().__init__(seed=seed)
        self.bus = MessageBus()
        self.inventory, self.books = inv, books
        self.customers, self.employees = customers, employees
        self.restock_threshold, self.restock_amount = restock_threshold, restock_amount
        self.steps = steps
        self.total_transactions = 0
        self.restock_events = 0
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
        """Handle a restock request by picking one employee (round-robin) and performing the restock once.
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

        # perform the restock centrally
        q = book.availableQuantity if book.availableQuantity is not None else 0
        restock_qty = getattr(self, 'restock_amount', 0) or 0
        book.availableQuantity = q + restock_qty

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
        # record transaction (+ SWRL may infer purchases)
        with onto:
            t = Transaction(f"T_{cust.name}_{book.name}_{self.total_transactions}")
            t.involves = [cust, book]
            cust.purchases.append(book)  # explicit assertion (works even without reasoner)
        self.total_transactions += 1
        self.bus.publish(TOPIC_PURCHASE_RES, {"status": "ok", "customer": cust, "book": book})
        # trigger restock if needed
        new_qty = book.availableQuantity if book.availableQuantity is not None else 0
        if new_qty < self.restock_threshold:
            self.bus.publish(TOPIC_RESTOCK_REQ, {"book": book})

    def step(self):
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
