# main.py
from ontology import onto, seed_data
from rules import run_reasoner_safely
from model import BookstoreModel

if __name__ == "__main__":
    inv, books, customers, employees = seed_data()
    sim = BookstoreModel(inv, books, customers, employees,
                         restock_threshold=3, restock_amount=4, steps=30, seed=42)
    sim.run()
    run_reasoner_safely()  # final pass

    print("\n=== SUMMARY ===")
    print("Total transactions:", sim.total_transactions)
    print("Restock events:", sim.restock_events)

    print("\nFinal stock by book:")
    for b in books:
        qty = b.availableQuantity if b.availableQuantity is not None else 0
        print(f"  {b.name:10s} | qty={qty} | ${b.hasPrice:.2f} | genres={list(b.hasGenre)}")

    print("\nCustomer purchases:")
    for c in onto.Customer.instances():
        bought = [bk.name for bk in c.purchases] if c.purchases else []
        print(f"  {c.name}: {bought}")

    # (Optional) list LowStock inferences if reasoner ran and any remain
    low = onto.LowStock.instances()
    print("\nLowStock (inferred):", [b.name for b in low] if low else [])

    # (Optional) persist ontology snapshot
    try:
        onto.save(file="bookstore.owl", format="rdfxml")
        print("\nSaved ontology to bookstore.owl")
    except Exception as e:
        print("Save failed:", e)
