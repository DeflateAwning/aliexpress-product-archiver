from datetime import datetime
from pathlib import Path
import time
import orjson
import requests
import re
import argparse

from selenium.webdriver import Chrome
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from loguru import logger


def extract_product_ids_from_file(file_with_ids: Path | str) -> list[int]:
    """Extract product IDs from a file, one per line."""
    if isinstance(file_with_ids, Path):
        file_content = file_with_ids.read_text()
    else:
        file_content = file_with_ids

    return [
        int(product_id_str)
        for product_id_str in re.findall(r"\b\d{6,}\b", file_content)
    ]


def scrape_product_page(driver: Chrome, product_id: int, save_location: Path) -> None:
    url = f"https://vi.aliexpress.com/item/{product_id}.html"
    logger.debug(f"Loading product info from {url}")

    # Navigate to the product page.
    driver.get(url)

    # Extract product information.
    product_info = load_product_info(driver, product_id)

    product_folder = save_location / str(product_id)
    product_folder.mkdir(parents=True, exist_ok=True)

    # Save product info to a JSON file.
    (product_folder / "info.json").write_bytes(
        orjson.dumps(product_info, option=orjson.OPT_INDENT_2)
    )
    logger.debug(f"Product info saved to {product_folder / 'info.json'}")

    # Save the product page HTML.
    with open(product_folder / "page.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)

    # Save product images.
    save_product_images(driver, product_folder)


def get_product_options_info(driver: Chrome) -> dict[str, list[str]]:
    """Extract product options from the product page.

    These are the Color/Size/Length/etc. options that are selectable on the product page.
    """
    options_dict: dict[str, list[str]] = {}

    def _add_option(prop_name: str, option: str) -> None:
        """Helper to add an option to the options_dict."""
        if prop_name not in options_dict:
            options_dict[prop_name] = []
        options_dict[prop_name].append(option.strip())

    property_blocks = driver.find_elements(
        By.XPATH, "//div[contains(@class, 'sku-item--property')]"
    )

    for block in property_blocks:
        # Extract the property name (e.g., Size, Length)
        title_el = block.find_element(
            By.XPATH, ".//div[contains(@class, 'sku-item--title')]//span"
        )
        property_name = title_el.text.split(":")[0].strip()

        # Extract options: either text spans or images (like for Color)
        option_elements = block.find_elements(
            By.XPATH,
            ".//div[contains(@class, 'sku-item--skus')]//div[contains(@class, 'sku-item--')]",
        )

        for option_element in option_elements:
            # Note: Would need to click on a bunch of the options to figure out the sold-out status.
            # is_sold_out = 'soldOut' in str(option_element.get_attribute("class"))
            property_value = "<CONFUSION>"

            # Prefer 'title' attribute, fallback to text or image alt
            title_attr = option_element.get_attribute("title")
            if title_attr:
                property_value = title_attr.strip()
            else:
                try:
                    img = option_element.find_element(By.TAG_NAME, "img")
                    alt = img.get_attribute("alt")
                    if alt:
                        property_value = alt.strip()
                    else:
                        logger.warning(
                            f"No title or alt found for option in {property_name}."
                        )
                except:
                    text = option_element.text.strip()
                    if text:
                        property_value = text
                    else:
                        logger.warning(f"No text found for option in {property_name}.")

            # if is_sold_out:
            #     property_value += " (Sold Out)"
            _add_option(property_name, property_value)

    return options_dict


def load_product_info(driver: Chrome, product_id: int) -> dict[str, str | int | dict]:
    # Get the product name, with wait.
    product_title_element = WebDriverWait(driver, 100000).until(
        EC.presence_of_element_located((By.XPATH, "//h1[@data-pl='product-title']"))
    )

    return {
        "product_id": product_id,
        "title": product_title_element.text.strip(),
        "options": get_product_options_info(driver),
        # TODO: Product description, price, etc.
    }


def save_product_images(driver: Chrome, product_folder: Path) -> None:
    """Save product images to the local filesystem.

    Expects the driver to be on a product page.
    """

    # Wait for thumbnail slider items (with wildcard class selector)
    wait = WebDriverWait(driver, 10)
    thumbnail_elements = wait.until(
        EC.presence_of_all_elements_located(
            (By.CSS_SELECTOR, "div[class^='slider--item--']")
        )
    )
    logger.debug(f"Found {len(thumbnail_elements)} thumbnail elements.")

    # Iterate over thumbnails
    for img_num, thumbnail_element in enumerate(thumbnail_elements, start=1):
        # Scroll and click
        # driver.execute_script("arguments[0].scrollIntoView(true);", thumbnail_element)

        # Scroll and JS click to avoid ElementClickInterceptedException
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", thumbnail_element
        )
        driver.execute_script("arguments[0].click();", thumbnail_element)

        thumbnail_element.click()

        # Wait for full-size image to load
        full_image = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "img[class^='magnifier--image--']")
            )
        )

        # Get image URL
        img_url = full_image.get_attribute("src")
        if not img_url:
            logger.warning(f"No image URL found for image {img_num}. Skipping.")
            continue
        print(f"Downloading image {img_num}: {img_url}")

        # Download image
        response = requests.get(img_url)

        # Decide where to save.
        img_suffix = img_url.split(".")[-1]
        with open(product_folder / f"image_{img_num:02}.{img_suffix}", "wb") as f:
            f.write(response.content)

        time.sleep(1)  # Give time for DOM updates.


def scrape_files(save_location: Path, file_with_ids: Path | str) -> None:
    driver = Chrome()
    logger.info("Starting scrape_files...")

    save_location = Path(f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

    product_ids = extract_product_ids_from_file(file_with_ids)
    logger.info(f"Extracted {len(product_ids)} product IDs.")

    already_done_product_ids: set[int] = set()
    for product_id in product_ids:
        if product_id in already_done_product_ids:
            logger.info(f"Skipping already processed product ID: {product_id}")
            continue

        scrape_product_page(driver, product_id=product_id, save_location=save_location)
        already_done_product_ids.add(product_id)

    driver.quit()
    logger.info("Finished scrape_files.")


def main():
    parser = argparse.ArgumentParser(description="Scrape AliExpress product pages.")
    parser.add_argument(
        "--input",
        dest="file_with_ids",
        type=str,
        help="Path to a file containing product IDs. Can also just be a string with IDs.",
    )
    parser.add_argument(
        "--save_location",
        type=Path,
        default=Path(f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}"),
        help="Directory to save the scraped product data.",
    )

    args = parser.parse_args()

    if Path(args.file_with_ids).is_file():
        logger.info(f"Reading product IDs from file: {args.file_with_ids}")
        file_with_ids = Path(args.file_with_ids)
    else:
        logger.info(f"Using provided string as product IDs: {args.file_with_ids}")
        file_with_ids = str(args.file_with_ids)

    scrape_files(save_location=args.save_location, file_with_ids=file_with_ids)


if __name__ == "__main__":
    main()
