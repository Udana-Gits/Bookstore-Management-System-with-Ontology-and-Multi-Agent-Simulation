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
    class hasGenre(DataProperty):                      range = [str]  
    class hasPrice(DataProperty, FunctionalProperty):  range = [float]
    class availableQuantity(DataProperty, FunctionalProperty): range = [int]

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
        
        # Create customers with more realistic names
        customer_names = ["Kasun_Silva", "Nimal_Perera", "Saman_Fernando", "Ruwan_Jayasinghe", "Chamari_Wijeratne"]
        customers = []
        for i in range(4):
            name = customer_names[i] if i < len(customer_names) else f"Customer_{i}"
            customers.append(Customer(name))
            
        employees = [Employee(f"Emp_{i}") for i in range(2)]
        for e in employees: e.worksAt = [inv]
    return inv, books, customers, employees
