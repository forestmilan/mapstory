#########################################################################
#
# Copyright (C) 2012 OpenPlans
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#
#########################################################################


import os
import tempfile
from .utils import sizeof_fmt
from celery.result import AsyncResult
from djcelery.models import TaskState
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db import models
from django.conf import settings
from geonode.layers.models import Layer
from jsonfield import JSONField
from .utils import GDALInspector, NoDataSourceFound

DEFAULT_LAYER_CONFIGURATION = {'configureTime': True,
                               'editable': True,
                               'convert_to_date': []}

IMPORTER_VALID_EXTENSIONS = getattr(settings, 'IMPORTER_VALID_EXTENSIONS',
                                    ['gpx', 'geojson', 'zip', 'tar', 'kml', 'csv'])


def validate_file_extension(value):
    """
    Validates file extensions.
    """
    for extension in IMPORTER_VALID_EXTENSIONS:
        if value.name.lower().endswith(extension):
            return
    raise ValidationError(u'Invalid File Type')


def validate_inspector_can_read(value):
    """
    Validates Geospatial data.
    """

    temp_directory = tempfile.mkdtemp()
    filename = os.path.join(temp_directory, value.name)

    with open(filename, 'wb') as f:
        for chunk in value.chunks():
            f.write(chunk)

    try:
        data = GDALInspector(filename).open()
    except NoDataSourceFound:
        raise ValidationError('Unable to locate geospatial data.')


class UploadedData(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True)
    state = models.CharField(max_length=16)
    date = models.DateTimeField('date', auto_now_add=True)
    upload_dir = models.CharField(max_length=100, null=True)
    name = models.CharField(max_length=64, null=True)
    complete = models.BooleanField(default=False)
    size = models.IntegerField(null=True, blank=True)
    metadata = models.TextField(null=True)
    file_type = models.CharField(max_length=50, null=True, blank=True)

    class Meta:
        ordering = ['-date']

    STATE_INVALID = 'INVALID'

    def get_delete_url(self):
        return reverse('data_upload_delete', args=[self.id])

    @property
    def filesize(self):
        """
        Humanizes the upload file size.
        """
        return sizeof_fmt(self.size)

    def file_url(self):
        """
        Exposes the file url.
        """
        return self.uploadfile_set.first().file.url

    def any_layers_imported(self):
        return any(self.uploadlayer_set.all().values_list('layer', flat=True))

    def all_layers_imported(self):
        return all(self.uploadlayer_set.all().values_list('layer', flat=True))

    def __unicode__(self):
        return 'Upload [%s] %s, %s' % (self.id, self.name, self.user)


class UploadLayer(models.Model):
    """
    Layers stored in an uploaded data set.
    """
    upload = models.ForeignKey(UploadedData, null=True, blank=True)
    index = models.IntegerField(default=0)
    name = models.CharField(max_length=64, null=True)
    fields = JSONField(null=True)
    layer = models.ForeignKey(Layer, blank=True, null=True, verbose_name='The linked GeoNode layer.')
    configuration_options = JSONField(null=True)
    task_id = models.CharField(max_length=36, blank=True, null=True)
    feature_count = models.IntegerField(null=True, blank=True)

    @property
    def layer_data(self):
        """
        Serialized information about the GeoNode layer.
        """
        if not self.layer:
            return

        return {'title': self.layer.title, 'url': self.layer.get_absolute_url(), 'id': self.layer.id}

    @property
    def description(self):
        """
        Serialized description of the layer.
        """

        params = dict(name=self.name, fields=self.fields, imported_layer=None, index=self.index, id=self.id)

        if self.layer:
            params['imported_layer'] = {'typename': self.layer.typename,
                                        'name': self.layer.name,
                                        'url': self.layer.get_absolute_url()}
        return params


    @property
    def status(self):
        """
        Returns the status of a single map page.
        """
        if self.task_id:
            try:
                return TaskState.objects.get(task_id=self.task_id).state
            except:
                return AsyncResult(self.task_id).status
        return 'UNKNOWN'

    class Meta:
        ordering = ('index',)


class UploadFile(models.Model):
    upload = models.ForeignKey(UploadedData, null=True, blank=True)
    file = models.FileField(upload_to="uploads", validators=[validate_file_extension, validate_inspector_can_read])
    slug = models.SlugField(max_length=250, blank=True)

    def __unicode__(self):
        return self.slug

    @property
    def name(self):
        return os.path.basename(self.file.path)

    def save(self, *args, **kwargs):
        self.slug = self.file.name
        super(UploadFile, self).save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self.file.delete(False)
        super(UploadFile, self).delete(*args, **kwargs)
