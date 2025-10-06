# ontology.py
from owlready2 import get_ontology, Thing, DataProperty, ObjectProperty, FunctionalProperty, destroy_entity

onto = get_ontology("http://example.org/bookstore.owl")

with onto:
    # Classes
    class Book(Thing): pass
    class Customer(Thing): pass
    class Employee(Thing): pass
    class Inventory(Thing): pass
    class Transaction(Thing): pass
    class LowStock(Thing): pass

    # Data properties
    class hasAuthor(DataProperty, FunctionalProperty): range = [str]
    class hasGenre(DataProperty):                      range = [str]  # multi-valued
    class hasPrice(DataProperty, FunctionalProperty):  range = [float]
    class availableQuantity(DataProperty, FunctionalProperty): range = [int]

    # Friendly display title (avoid using the RDF name/IRI for human titles)
    class title(DataProperty, FunctionalProperty): range = [str]

    # Object properties
    class purchases(ObjectProperty): domain = [Customer]; range = [Book]
    class worksAt(ObjectProperty):   domain = [Employee]; range = [Inventory]
    class involves(ObjectProperty):  domain = [Transaction]; range = [Thing]

def seed_data():
    import random
    with onto:
        # Clear previous instances to avoid IRI/name collisions on repeated runs/reset
        for cls in (Book, Customer, Employee, Inventory, Transaction, LowStock):
            for inst in list(cls.instances()):
                try:
                    destroy_entity(inst)
                except Exception:
                    pass
        inv = Inventory("MainInventory")
        
        # Practical book data with realistic Sri Lankan prices (LKR 1000-3000) and Sinhala names
        book_data = [
            {"title": "Madol Doova", "author": "Martin Wickramasinghe", "genres": ["Fiction"], "price": 1250.00},
            {"title": "Gamperaliya", "author": "Martin Wickramasinghe", "genres": ["Fiction"], "price": 2400.00},
            {"title": "Viragaya", "author": "Martin Wickramasinghe", "genres": ["Fiction"], "price": 1850.00},
            {"title": "Golu Hadawatha", "author": "Karunasena Jayalath", "genres": ["Romance", "Fiction"], "price": 1650.00},
            {"title": "Malagiya Aththo", "author": "Ediriweera Sarachchandra", "genres": ["Fiction"], "price": 2200.00},
            {"title": "Samsaranye Urumaya", "author": "Simon Nawagattegama", "genres": ["Fiction"], "price": 1450.00}
        ]
        
        books = []
        for i, book_info in enumerate(book_data):
            b = Book(f"Book_{i}")
            # store human-friendly title in a data property to avoid renaming the individual
            b.title = book_info["title"]
            b.hasAuthor = book_info["author"]
            b.hasGenre = book_info["genres"]
            b.hasPrice = book_info["price"]  # Price already in practical LKR range
            b.availableQuantity = random.randint(1, 6)
            books.append(b)
        
        customers = [Customer(f"Cust_{i}") for i in range(4)]
        employees = [Employee(f"Emp_{i}") for i in range(2)]
        for e in employees: e.worksAt = [inv]
    return inv, books, customers, employees
