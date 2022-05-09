# repository-excerpt
This repo contains selected items from a private project, Digital Repository. 

<p><b>models.py</b> contains Django models from Digital Repository and underlying business process management system
<p><b>filters.py</b> contains custom filters for Django Rest Framework with <b>AssetFilterBackend</b> being the most functional (and cumbersome) of all. It also depends on ElasticSearch module not present in listing.
<p><b>custom_stations.py</b> provides an extension mechanism to Station model - it allows for custom code execution based on Station.classname to instantiate the correct extension and fire it's perform() method.
<p><b>permissions.py</b> contains routines that allow for variable-depth permission check: depending on current user's attributes (username, group membership, IP) and a set of existing Permission objects, a permission to an object or a set of ojjects is granted or denied. Variable-depth allows for any level of generalization: from "<i>give this specific user an access to this object</i>" to "<i>allow downloading of the materials belonging to this category for users within specific IP range for the next 10 days</i>" or "<i>grant everyone access to metadata of the materials that has payload field <b>license</b> set to <b>public</b></i>".
<p><b>serializers.py</b> contains serializers for every model in models.py.
<p><b>doi_helpers.py</b> contains routines that access remote API for minting DOIs (Digital Object Identifiers).
