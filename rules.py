# rules.py
from owlready2 import Imp, sync_reasoner, destroy_entity
from ontology import onto

LOW_STOCK_THRESHOLD = 3

def setup_rules():
    with onto:
        
        rule_purch = Imp()
        rule_purch.set_as_rule(
            "Transaction(?t) ^ involves(?t, ?c) ^ involves(?t, ?b) ^ Customer(?c) ^ Book(?b) -> purchases(?c, ?b)"
        )

def check_low_stock():
    """Manually check for low stock and create LowStock instances"""
    with onto:
        # Clear existing LowStock instances
        for ls in list(onto.LowStock.instances()):
            destroy_entity(ls)
        
        # Check each book and create LowStock if needed
        for book in onto.Book.instances():
            qty = book.availableQuantity if book.availableQuantity is not None else 0
            if qty < LOW_STOCK_THRESHOLD:
                # Create LowStock instance if it doesn't exist
                low_stock_name = f"LowStock_{book.name}"
                if not hasattr(onto, low_stock_name):
                    ls = onto.LowStock(low_stock_name)


def run_reasoner_safely():
    try:
        check_low_stock()  
        with onto:
            sync_reasoner()  
    except Exception:
        pass

# Set up rules when module is imported
setup_rules()
