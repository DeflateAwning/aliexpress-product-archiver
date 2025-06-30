from datetime import datetime
from pathlib import Path
import random
import time
import backoff
import orjson
import requests
import re
import argparse
import base64

from selenium.webdriver.common.print_page_options import PrintOptions
from selenium.common.exceptions import TimeoutException
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


@backoff.on_exception(
    backoff.constant,
    Exception,
    max_tries=3,
    interval=10,
    on_backoff=lambda details: logger.warning(
        f"Retrying due to error: {details.get('exception')}"
    ),
)
def scrape_product_page(driver: Chrome, product_id: int, save_location: Path) -> None:
    """Scrape all info about a product page."""

    url = f"https://vi.aliexpress.com/item/{product_id}.html"
    logger.info(f"Loading product info from {url}")

    # Navigate to the product page.
    driver.get(url)

    product_folder = save_location / str(product_id)
    product_folder.mkdir(parents=True, exist_ok=True)

    # Ensure the page is mostly loaded, then click all the "View More" buttons.
    if wait_for_product_title_to_load_and_get_it(driver) == "Not Found":
        logger.error(f"Product ID {product_id} not found. Skipping.")

        # Save product info to a JSON file.
        product_info = {
            "product_id": product_id,
            "title": "Not Found",
            "options": {},
            "specifications": {},
            "description": "",
        }
        (product_folder / "info.json").write_bytes(
            orjson.dumps(product_info, option=orjson.OPT_INDENT_2)
        )
        return

    time.sleep(0.5)
    click_all_view_more_buttons(driver)

    # Extract product information.
    product_info = load_product_info(driver, product_id)

    # Save product info to a JSON file.
    (product_folder / "info.json").write_bytes(
        orjson.dumps(product_info, option=orjson.OPT_INDENT_2)
    )
    logger.debug("Product info saved.")

    # Save the product page HTML.
    with open(product_folder / "page.html", "w", encoding="utf-8") as f:
        f.write(driver.page_source)

    # Print the page as a PDF (best way to see the Description section).
    print_options = PrintOptions()
    pdf_base64 = driver.print_page(print_options)
    with open(product_folder / "print.pdf", "wb") as pdf_file:
        pdf_file.write(base64.b64decode(pdf_base64))

    # Save product images.
    save_product_images(driver, product_folder)
    save_images_in_description(driver, product_folder)


def wait_for_product_title_to_load_and_get_it(driver: Chrome) -> str:
    """Wait for the product title to load and return it.

    Also handles other cases, like if the product is deleted.

    Returns "Not Found" if the product is not available.
    """
    for try_num in range(max_retry := 200):
        # First try to get the product title element.
        try:
            product_title_element = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//h1[@data-pl='product-title']")
                )
            )
        except TimeoutException as e:
            logger.warning(f"Attempt {try_num + 1}: Product title not found yet: {e}")
            time.sleep(0.5)
        else:
            return product_title_element.text.strip()

        # Check if the page has a "Not Found" element.
        try:
            # Check if a "Not Found" element appears first
            not_found = WebDriverWait(driver, 5).until(
                EC.presence_of_element_located(
                    (By.XPATH, "//div[contains(@class, 'not-found--desc')]")
                )
            )
        except TimeoutException:
            pass  # "Not Found" element did not appear; continue to wait for the product title.
        else:
            if not_found:
                return "Not Found"

        logger.warning(
            f"Might be stuck on a slow-loading page, or it's waiting for a captcha. Retrying ({try_num + 1}/{max_retry})..."
        )
        if try_num % 10 == 9:
            logger.info("Prompting for using input before retrying...")
            input("You likely need to solve a captcha. Press Enter to continue retrying...")

    raise RuntimeError("Product title did not load after 100 attempts.")


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
                except Exception:
                    text = option_element.text.strip()
                    if text:
                        property_value = text
                    else:
                        logger.warning(f"No text found for option in {property_name}.")

            # if is_sold_out:
            #     property_value += " (Sold Out)"
            _add_option(property_name, property_value)

    return options_dict


def click_all_view_more_buttons(driver: Chrome) -> None:
    # Nominally, these buttons are at the Description and Specifications sections.

    try:
        # Find all <button> elements whose span contains 'View more' or 'Show more'.
        # Exclude the Review ones, as they open pop-ups that break everything.
        buttons = driver.find_elements(
            By.XPATH,
            "//button[.//span[contains(text(), 'View more') or contains(text(), 'Show more')] and not(ancestor::*[contains(@class, 'review--wrap')])]",
        )

        logger.debug(f'Found {len(buttons)} "View more" button(s).')
        if not buttons:
            logger.warning("No 'View more' buttons found on the page. Weird.")

        for i, button in enumerate(buttons):
            try:
                # Scroll into view before clicking (in case it's off-screen)
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", button
                )
                time.sleep(0.5)  # Optional wait for animations or visibility

                button.click()
                logger.debug(f'Clicked "View more" button #{i + 1}')

                # Optional wait in case content loads dynamically
                time.sleep(1)
            except Exception as e:
                logger.warning(f"Could not click button #{i + 1}: {e}")

    except Exception as e:
        logger.warning(f'Error finding "View more" buttons: {e}')


def get_product_specifications(driver: Chrome) -> dict[str, str]:
    # Get key-value Specifications section.
    specs = {}

    # Locate the list items in the <ul>
    spec_list_items = driver.find_elements(
        By.CSS_SELECTOR, "ul[class^='specification--list'] > li"
    )
    for spec_list_item in spec_list_items:
        # Find all title-desc pairs within each <li>
        spec_titles = spec_list_item.find_elements(
            By.CSS_SELECTOR, "div[class^='specification--title'] span"
        )
        spec_descriptions = spec_list_item.find_elements(
            By.CSS_SELECTOR, "div[class^='specification--desc'] span"
        )

        for title, desc in zip(spec_titles, spec_descriptions):
            title_str = title.text.strip()
            desc_str = desc.text.strip()

            if title_str in specs:
                while title_str in specs:
                    title_str += " (Duplicate)"

            specs[title_str] = desc_str

    return specs


def load_product_info(driver: Chrome, product_id: int) -> dict[str, str | int | dict]:
    return {
        "product_id": product_id,
        "title": wait_for_product_title_to_load_and_get_it(driver),
        "options": get_product_options_info(driver),
        "specifications": get_product_specifications(driver),
        "description": driver.find_element(By.ID, "product-description").text.strip(),
        # TODO: Price, etc.
    }


@backoff.on_exception(backoff.expo, Exception, max_tries=5)
def download_image_with_retries(
    img_url: str, save_folder: Path, img_path_stem: str
) -> None:
    img_url = img_url.strip("/")
    img_response = requests.get(img_url)
    img_suffix = img_url.split(".")[-1].split("?")[0]
    with open(save_folder / f"{img_path_stem}.{img_suffix}", "wb") as f:
        f.write(img_response.content)


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
        # Scroll and JS click to avoid ElementClickInterceptedException.
        # Then, click on the thumbnail to open the full-size image.
        driver.execute_script(
            "arguments[0].scrollIntoView({block: 'center'});", thumbnail_element
        )
        driver.execute_script("arguments[0].click();", thumbnail_element)
        thumbnail_element.click()

        time.sleep(0.5)  # Allow time for the full-size image to load.

        # Wait for full-size image to load
        full_image = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "img[class^='magnifier--image--']")
            )
        )

        # Get image URL.
        img_url = full_image.get_attribute("src")
        if not img_url:
            logger.warning(f"No image URL found for image {img_num}. Skipping.")
            continue
        logger.info(f"Downloading image {img_num}: {img_url}")

        # Download the image with retries.
        try:
            download_image_with_retries(
                img_url=img_url,
                save_folder=product_folder,
                img_path_stem=f"img_{img_num:02}",
            )
        except Exception as e:
            logger.warning(f"Failed to download image {img_num}: {e}")
            continue


def save_images_in_description(driver: Chrome, product_folder: Path) -> None:
    container = driver.find_element(By.ID, "product-description")
    images = container.find_elements(By.TAG_NAME, "img")
    logger.debug(f"Found {len(images)} images in the product description.")

    # Save each image
    for img_num, img in enumerate(images, start=1):
        img_url = img.get_attribute("src")
        if not img_url:
            logger.warning(f"Description image #{img_num} has no URL. Skipping.")
            continue
        logger.info(f"Downloading description image {img_num}: {img_url}")

        try:
            download_image_with_retries(
                img_url=img_url,
                save_folder=product_folder,
                img_path_stem=f"desc_{img_num:02}",
            )
        except Exception as e:
            logger.warning(f"Failed to download description image {img_num}: {e}")
            continue


def scrape_files(
    save_location: Path, file_with_ids: Path | str, enable_shuffle: bool
) -> None:
    driver = Chrome()
    logger.info("Starting scrape_files...")

    product_ids = extract_product_ids_from_file(file_with_ids)
    logger.info(f"Extracted {len(product_ids)} product IDs.")

    if enable_shuffle:
        random.shuffle(product_ids)

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
    default_save_path = Path(f"products_{datetime.now().strftime('%Y%m%d_%H%M%S')}")

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
        default=default_save_path,
        help="Directory to save the scraped product data.",
    )
    parser.add_argument(
        "--shuffle",
        action="store_true",
        help="Shuffle the product IDs before scraping. Useful for randomizing the order of scraping.",
    )

    args = parser.parse_args()

    if Path(args.file_with_ids).is_file():
        logger.info(f"Reading product IDs from file: {args.file_with_ids}")
        file_with_ids = Path(args.file_with_ids)
    else:
        logger.info(f"Using provided string as product IDs: {args.file_with_ids}")
        file_with_ids = str(args.file_with_ids)

    # Ensure the save location exists.
    save_location = Path(args.save_location)
    save_location.mkdir(parents=True, exist_ok=True)

    log_file = save_location / "scrape_log.txt"
    if log_file.exists():
        logger.warning(
            "Data is already stored to the output directory. This scraper works better with a fresh directory."
        )

    # Set up logging to a file in the save location.
    logger.add(log_file)

    scrape_files(
        save_location=save_location,
        file_with_ids=file_with_ids,
        enable_shuffle=args.shuffle,
    )


if __name__ == "__main__":
    main()
