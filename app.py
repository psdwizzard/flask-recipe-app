import os
from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from recipe_scrapers import scrape_me
from notion_client import Client
from dotenv import load_dotenv  # Securely load API keys

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# Configure the SQLite database
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = f"sqlite:///{os.path.join(BASE_DIR, 'recipes.db')}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize database and migrations
db = SQLAlchemy(app)
migrate = Migrate(app, db)

# Set up Notion API using environment variables
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

notion = Client(auth=NOTION_TOKEN)

# Many-to-Many Relationship Table for Recipe Lists
recipe_list_association = db.Table(
    'recipe_list_association',
    db.Column('recipe_id', db.Integer, db.ForeignKey('recipe.id'), primary_key=True),
    db.Column('list_id', db.Integer, db.ForeignKey('recipe_list.id'), primary_key=True)
)

# Recipe Model
class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(150), nullable=False)
    ingredients = db.Column(db.Text, nullable=False)
    instructions = db.Column(db.Text, nullable=False)
    image = db.Column(db.String(250))
    author = db.Column(db.String(150))
    source_url = db.Column(db.String(250))
    average_rating = db.Column(db.Float, default=0.0, nullable=False)
    rating_count = db.Column(db.Integer, default=0, nullable=False)
    notion_synced = db.Column(db.Boolean, default=False)  # Track if synced to Notion

# Recipe List Model
class RecipeList(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    recipes = db.relationship(
        'Recipe', secondary=recipe_list_association,
        backref=db.backref('lists', lazy='dynamic')
    )

# Function to Sync Recipe to Notion
def send_recipe_to_notion(recipe):
    """Send a recipe to Notion as a page."""
    try:
        print(f"🔄 Syncing '{recipe.title}' to Notion...")

        properties = {
            "Title": {"title": [{"text": {"content": recipe.title}}]}
        }

        # Fix Image URL Format
        if recipe.image:
            properties["Image URL"] = {
                "files": [{"name": "Recipe Image", "external": {"url": recipe.image}}]
            }

        # Fix Source URL Format
        if recipe.source_url:
            properties["Source URL"] = {"url": recipe.source_url}

        response = notion.pages.create(
            parent={"database_id": NOTION_DATABASE_ID},
            properties=properties,
            children=[
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"text": {"content": "Ingredients"}}]},
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": recipe.ingredients}}]},
                },
                {
                    "object": "block",
                    "type": "heading_2",
                    "heading_2": {"rich_text": [{"text": {"content": "Instructions"}}]},
                },
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"text": {"content": recipe.instructions}}]},
                }
            ]
        )

        # Mark recipe as synced
        recipe.notion_synced = True
        db.session.commit()

        print(f"✅ Successfully synced '{recipe.title}' to Notion!")
    except Exception as e:
        print(f"❌ Failed to sync '{recipe.title}': {e}")


# Home Page
@app.route("/")
def index():
    recipes = Recipe.query.all()
    return render_template("index.html", recipes=recipes)

# Extract Recipe (Supports Bulk Import & Notion Sync)
@app.route("/extract", methods=["GET", "POST"])
def extract():
    if request.method == "POST":
        urls = request.form.get("urls")
        list_id = request.form.get("list_id")  # Get selected list ID (if any)
        url_list = [url.strip() for url in urls.splitlines() if url.strip()]

        added_recipes = 0
        skipped_recipes = 0

        for url in url_list:
            try:
                scraper = scrape_me(url)
                title = scraper.title()
                ingredients = "\n".join(scraper.ingredients())
                instructions = scraper.instructions()
                image = scraper.image()
                author = scraper.author() or "Unknown"
                source_url = url

                # **Check if the recipe already exists**
                existing_recipe = Recipe.query.filter_by(title=title).first()

                if existing_recipe:
                    print(f"⚠️ Skipping '{title}' (already exists)")
                    skipped_recipes += 1
                    continue  # Skip to the next recipe

                # **Create and save the new recipe**
                new_recipe = Recipe(
                    title=title, ingredients=ingredients, instructions=instructions,
                    image=image, author=author, source_url=source_url
                )
                db.session.add(new_recipe)
                db.session.commit()

                # **If a list was selected, add the recipe to the list**
                if list_id:
                    recipe_list = RecipeList.query.get(list_id)
                    if recipe_list:
                        recipe_list.recipes.append(new_recipe)
                        db.session.commit()

                added_recipes += 1

            except Exception as e:
                print(f"❌ Error importing {url}: {e}")

        print(f"✅ Added {added_recipes} new recipes, ⚠️ Skipped {skipped_recipes} duplicates.")

        return redirect(url_for("recipes_page"))

    return render_template("extract.html")


# Sync Only New Recipes to Notion
@app.route("/sync_notion")
def sync_notion():
    new_recipes = Recipe.query.filter_by(notion_synced=False).all()
    for recipe in new_recipes:
        send_recipe_to_notion(recipe)
    return redirect(url_for("index"))

# Force Sync Button in Navbar
@app.route("/force_sync")
def force_sync():
    return sync_notion()

# Recipe List Page
@app.route("/recipes")
def recipes_page():
    query = request.args.get("query", "")
    if query:
        recipes = Recipe.query.filter(
            (Recipe.title.ilike(f"%{query}%")) | (Recipe.ingredients.ilike(f"%{query}%"))
        ).all()
    else:
        recipes = Recipe.query.all()
    return render_template("recipes.html", recipes=recipes)

# Delete a Recipe
@app.route("/delete_recipe/<int:recipe_id>", methods=["POST"])
def delete_recipe(recipe_id):
    """Delete a recipe from the database."""
    recipe = Recipe.query.get_or_404(recipe_id)

    # Remove the recipe from all associated lists
    for recipe_list in recipe.lists:
        recipe_list.recipes.remove(recipe)

    db.session.delete(recipe)
    db.session.commit()

    print(f"🗑️ Deleted recipe: {recipe.title}")
    return redirect(url_for("recipes_page"))


# Lists Page
@app.route("/lists")
def lists():
    all_lists = RecipeList.query.all()
    return render_template("lists.html", lists=all_lists)

# Add a Recipe to a List
@app.route("/add_recipe_to_list", methods=["POST"])
def add_recipe_to_list():
    recipe_id = request.form.get("recipe_id")
    list_id = request.form.get("list_id")
    if recipe_id and list_id:
        recipe = Recipe.query.get(recipe_id)
        recipe_list = RecipeList.query.get(list_id)
        if recipe and recipe_list:
            if recipe not in recipe_list.recipes:
                recipe_list.recipes.append(recipe)
                db.session.commit()
    return redirect(url_for('recipes_page'))

# Context Processor for Navbar Button
@app.context_processor
def inject_lists():
    all_lists = RecipeList.query.all()
    return dict(all_lists=all_lists)
@app.route("/list/<int:list_id>")
def view_list(list_id):
    recipe_list = RecipeList.query.get_or_404(list_id)
    return render_template("view_list.html", recipe_list=recipe_list)
@app.route("/recipe/<int:recipe_id>")
def recipe_detail(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    return render_template("recipe_detail.html", recipe=recipe)
@app.route("/rate_recipe/<int:recipe_id>", methods=["POST"])
def rate_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    rating = request.form.get("rating")

    if rating:
        rating = int(rating)

        # Ensure default values exist
        if recipe.average_rating is None:
            recipe.average_rating = 0.0
        if recipe.rating_count is None:
            recipe.rating_count = 0

        # Update rating using weighted average formula
        total_score = (recipe.average_rating * recipe.rating_count) + rating
        recipe.rating_count += 1
        recipe.average_rating = total_score / recipe.rating_count

        db.session.commit()

    return redirect(url_for("recipe_detail", recipe_id=recipe.id))
@app.route("/edit_recipe/<int:recipe_id>", methods=["GET", "POST"])
def edit_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    
    if request.method == "POST":
        recipe.title = request.form.get("title")
        recipe.ingredients = request.form.get("ingredients")
        recipe.instructions = request.form.get("instructions")
        recipe.image = request.form.get("image")
        db.session.commit()
        return redirect(url_for("recipe_detail", recipe_id=recipe.id))
    
    return render_template("edit_recipe.html", recipe=recipe)
@app.route("/add_list", methods=["POST"])
def add_list():
    name = request.form.get("name")
    if name:
        new_list = RecipeList(name=name)
        db.session.add(new_list)
        db.session.commit()
    return redirect(url_for("lists"))
@app.route("/delete_list/<int:list_id>", methods=["POST"])
def delete_list(list_id):
    recipe_list = RecipeList.query.get_or_404(list_id)
    db.session.delete(recipe_list)
    db.session.commit()
    return redirect(url_for("lists"))
@app.route("/remove_recipe_from_list", methods=["POST"])
def remove_recipe_from_list():
    recipe_id = request.form.get("recipe_id")
    list_id = request.form.get("list_id")

    if recipe_id and list_id:
        recipe = Recipe.query.get(recipe_id)
        recipe_list = RecipeList.query.get(list_id)
        if recipe and recipe_list:
            if recipe in recipe_list.recipes:
                recipe_list.recipes.remove(recipe)
                db.session.commit()
    
    return redirect(url_for("view_list", list_id=list_id))
@app.route("/search", methods=["GET"])
def search_results():
    query = request.args.get("query", "").strip()
    list_id = request.args.get("list_id", "").strip()

    if not query:
        return redirect(url_for("lists"))  # Redirect to lists page if no query

    if not list_id.isdigit():  # Ensure list_id is a valid integer
        return redirect(url_for("lists"))

    list_id = int(list_id)  # Convert list_id to an integer
    recipe_list = RecipeList.query.get_or_404(list_id)

    # Filter recipes by title or ingredients within the selected list
    filtered_recipes = [
        recipe for recipe in recipe_list.recipes
        if query.lower() in recipe.title.lower() or query.lower() in recipe.ingredients.lower()
    ]

    return render_template("view_list.html", recipe_list=recipe_list, search_query=query, search_results=filtered_recipes)

# Run Flask App
if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    app.run(debug=True)
