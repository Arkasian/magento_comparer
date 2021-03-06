import json
import logging
import sys
import numpy

from serializer import Serializer
from rest_client import RestClient
from file_loader import FileLoader
from converter_factory import TypeConverterFactory
from converter_rule import ConverterRule


class Main:
    config = dict()
    print_to_stdout: bool = True

    converter_rules = {
        "int": "0:False;range(1, sys.maxsize):True",
        "float": "0.0:False;numpy.arange(0.1, 100000.0, 0.1):True",
        "bool": "False:0;True:1"
    }

    def __init__(self, config):
        self.config = config

    def get_dummy_product(self, item):
        return {
            "product": {
                "sku": item[1].text,
                "price": 0,
                "name": None,
                "status": 1,
                "visibility": 4,
                "type_id": "simple",
                "attribute_set_id": 4,
                "custom_attributes": {
                    "special_price": 0
                }
            }
        }

    def link_products(self, magento_product, commodity):
        attributes_dict = dict(self.config["attributes"])

        for attribute in attributes_dict:
            read_value = self.read_value_from_xml(commodity, attributes_dict[attribute])
            self.process_magento_object(magento_product, attribute.split("/"), read_value)

        logging.debug(magento_product)
        return magento_product

    def process_magento_object(self, magento_product, key_sequence, value_from_xml):
        level = magento_product

        if key_sequence:
            if isinstance(key_sequence, list):
                key_path = ""
                result = self.separate_type(key_sequence)
                key_sequence = result[1]

                for key in key_sequence:
                    if key is not None:
                        key_path = key_path.join(str(key).join("/"))
                        type_name = result[0]

                        if isinstance(level, dict):
                            actual_type_name = eval(value_from_xml).__class__.__name__
                            if key_sequence.index(key) == len(key_sequence) - 1:
                                if type_name != actual_type_name:
                                    level[key] = self.convert_to_type(actual_type_name, value_from_xml)
                                else:
                                    level[key] = eval(value_from_xml)
                            elif key in level:
                                level = level[key]
                            elif key not in level:
                                # TODO: It seems to be a good idea to add some kind of type recognition here,
                                #  recognition between list and dict.
                                level[key] = dict()
                                level = level[key]
                            else:
                                continue
                        else:
                            logging.info("Is not an instance of dict, trying as list.")

                            if isinstance(level, list):
                                if key in level:
                                    tmp = self.get_special_attr(level, str(key))
                                    index = level.index(tmp)
                                    if "value" in tmp:
                                        tmp["value"] = value_from_xml
                                        level[index] = tmp

                                    else:
                                        logging.error("This does not seem to be an attribute object.")
                                else:
                                    continue

                            else:
                                logging.info("Dont fucking know what is this?")
                else:
                    logging.debug("Given object is not a list of keys.")
            else:
                logging.debug("List of keys is null.")

    def read_value_from_xml(self, commodity_element, tag_sequence):
        tmp = commodity_element
        element = tmp.findall(tag_sequence)[0]
        return element.text

    def get_special_attr(self, attribute_list, attr_name):
        for attr in attribute_list:
            if attr["attribute_code"] == attr_name:
                return attr

    def get_product_list(self, headers):
        rest_client = RestClient()
        products = rest_client.send_get(
            self.config["global"]["host"] + "/rest/" + self.config["global"]["store"]
            + "/V1/products?searchCriteria[filter_groups][0][filters][0][field]=sku&"
              "searchCriteria[filter_groups][0][filters][0][condition_type]=notnull",
            headers, None)
        return products

    def authorize_user(self, rest_client):
        user = {
            "username": self.config["global"]["username"],
            "password": self.config["global"]["password"]
        }

        response = rest_client.send_post(self.config["global"]["host"] + "/rest/V1/integration/admin/token",
                                         {"Content-Type": "application/json"},
                                         json.dumps(user)
                                         )

        if response[1] != 200:
            logging.error("Error authorizing user.")

        return response[0]

    def prepare_different_items_list(self, rest_client, headers):
        serializer = Serializer()
        url = self.config["global"]["xml_url"]
        xml = rest_client.send_get_binary(url, None, None)
        read_item_list = list()
        if xml[1] != 200:
            logging.info("No file found under the URI: %s, exiting now..." % str(url))
            exit(-1)

        root_item = serializer.deserialize_xml(str(xml[0], "utf8"))
        commodities = root_item

        magento_product_list = list(self.get_product_list(headers)[0]["items"])
        magento_product_dict = dict()

        for magento_product in magento_product_list:
            magento_product_dict[magento_product["sku"]] = magento_product

        for commodity in commodities:
            if commodity[1].text:
                if commodity[1].text in magento_product_dict:
                    magento_product = magento_product_dict[commodity[1].text]
                    result_magento_product = self.link_products(magento_product, commodity)

                    if result_magento_product:
                        read_item_list.append(result_magento_product)
                else:
                    logging.warning("There is no product of sku {0} in Magento database.".format(commodity[1].text))

        return read_item_list

    def update_magento_products(self, rest_client, headers, different_products_list):
        logging.info("Starting update products procedure.")
        for product in different_products_list:
            if product:
                product = {
                    "product": product
                }
                json_content = json.dumps(product)
                magento_product = \
                    rest_client.send_get(
                        self.config["global"]["host"] + "/rest/" + self.config["global"]["store"] + "/V1/products/"
                        + product["product"]["sku"],
                        headers, None)[0]
                attribute = self.get_special_attr(magento_product["custom_attributes"], "special_price")
                special_price = 0.0

                if attribute:
                    special_price = float(attribute["value"])
                    logging.info(
                        "Processing product: {0}, changing old values [{1},{2}] to the following: price = {3}, "
                        "special price = {4} "
                        .format(product["product"]["sku"],
                                magento_product["price"],
                                special_price,
                                product["product"]["price"],
                                attribute["value"]))

                response = rest_client.send_put(
                    self.config["global"]["host"] + "/rest/" + self.config["global"]["store"] + "/V1/products/"
                    + product["product"]["sku"],
                    headers,
                    json_content
                )

                if response[1] == 200:
                    logging.info("Successfully updated product.")
                else:
                    logging.error("Server answered with {0} code, message: {1}".format(response[1], response[0]))
        else:
            logging.info("No different products found between xml file and magento db.")

    logging.info("Update products procedure ended.")

    def add_dummy_products(self, rest_client, headers):
        serializer = Serializer()
        xml = rest_client.send_get_binary(self.config["global"]["xml_url"], None, None)
        root_item = serializer.deserialize_xml(str(xml[0], "utf8"))
        commodities = root_item

        for commodity in commodities:
            product = self.get_dummy_product(commodity)
            json_content = json.dumps(product)
            json_data = rest_client.send_post(self.config["global"]["host"] + "/rest/" + self.config["global"]["store"]
                                              + "/V1/products/",
                                              headers,
                                              json_content
                                              )
            if json_data[1] != 200:
                if "message" in dict(json_data[0]):
                    logging.error(json_data[0]["message"])
                else:
                    logging.error(json.dumps(json_data[0]))

    def main(self):
        self.configure_logging()
        logging.getLogger("main.stdout").info("Logging to file " + self.config["global"]["log_path"]
                                              + 'magento_updater.log')

        mode = "prod"
        if self.config["global"]["mode"] is not None:
            mode = self.config["global"]["mode"]
        rest_client = RestClient()
        token = self.authorize_user(rest_client)
        headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token
        }

        if mode == "devel":
            self.add_dummy_products(rest_client, headers)

        logging.info("User has been successfully logged in.")
        read_item_list = self.prepare_different_items_list(rest_client, headers)
        self.update_magento_products(rest_client, headers, read_item_list)
        logging.info("Job done, exiting. Goodbye.")

    def configure_logging(self):
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        fh = logging.FileHandler(filename=self.config["global"]["log_path"] + 'magento_updater.log')
        fh.setLevel(self.config["global"]["log_level"])
        fh.setFormatter(formatter)
        root.addHandler(fh)

        # TODO: There is some shit with loggers to be done if the cpu usage is too high because of logging to stdout.

    def separate_type(self, key_sequence):
        tmp = str(key_sequence[0]).split("]")
        type_string = tmp[0][1:]
        key_sequence[0] = tmp[1]
        return type_string, key_sequence

    def convert_to_type(self, from_type, value_from_xml):
        converter_factory = TypeConverterFactory()
        if from_type in self.converter_rules:
            converter_expression = self.converter_rules[from_type]
            converter_rule = ConverterRule(converter_expression)
            converter = converter_factory.create(from_type, converter_rule)
            result = converter.convert(value_from_xml)
        else:
            result = value_from_xml
        return result


def match_keys(attributes: list, config: dict):
    attributes_result: dict = dict()
    if attributes:
        attributes_dict: dict = config["attributes"]
        key_count = len(attributes_dict.keys())
        for i in range(0, key_count):
            key = list(attributes_dict.keys())[i]
            value_key = str(key).split("]")[1]

            if value_key in attributes_list:
                attributes_result[key] = attributes_dict[key]
        config["attributes"] = attributes_result


if __name__ == '__main__':
    fileLoader = FileLoader()
    fileStrContent = fileLoader.loadFile(sys.argv[1])
    attributes_list: list = str(sys.argv[2]).split(",")
    configObject = json.loads(fileStrContent)
    match_keys(attributes_list, configObject)
    m = Main(configObject)
    m.main()
