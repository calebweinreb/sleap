"""
Module for gui command context and commands objects.
"""

import attr
import operator
import os

from abc import ABC
from enum import Enum
from pathlib import PurePath
from typing import Callable, Dict, Iterator, List, Optional, Type, Tuple

import numpy as np

from PySide2 import QtCore, QtWidgets

from PySide2.QtWidgets import QMessageBox

from sleap.gui.dialogs.delete import DeleteDialog
from sleap.skeleton import Skeleton
from sleap.instance import Instance, PredictedInstance, Point, Track, LabeledFrame
from sleap.io.video import Video
from sleap.io.dataset import Labels
from sleap.gui.dialogs.importvideos import ImportVideos
from sleap.gui.dialogs.filedialog import FileDialog
from sleap.gui.dialogs.missingfiles import MissingFilesDialog
from sleap.gui.dialogs.merge import MergeDialog
from sleap.gui.dialogs.message import MessageDialog
from sleap.gui.suggestions import VideoFrameSuggestions
from sleap.gui.state import GuiState


OPEN_IN_NEW = True


class UpdateTopic(Enum):
    all = 1
    video = 2
    skeleton = 3
    labels = 4
    on_frame = 5
    suggestions = 6
    tracks = 7
    frame = 8
    project = 9
    project_instances = 10


class AppCommand(ABC):
    """Abstract Base Class for Commands.

    Attributes:
        topics: List of `UpdateTopic` items. Override this to indicate what
            should be updated after command is executed.
        does_edits: Whether command will modify data that could be saved.
    """

    topics = []
    does_edits = False

    def execute(self, context: "CommandContext", params=None):
        """Entry point for running command.

        This calls internal methods to gather information required for
        execution, perform the action, and notify about changes.

        Ideally, any information gathering should be performed in the `ask`
        method, and be added to the `params` dictionary which then gets
        passed to `do_action`. The `ask` method should not modify state.

        (This will make it easier to add support for undo,
        using an `undo_action` which will be given the same `params`
        dictionary.)

        If it's not possible to easily separate information gathering from
        performing the action, the child class should implement `ask_and_do`,
        which it turn should call `do_with_signal` to notify about changes.

        Args:
            context: This is the `CommandContext` in which the command will
                execute. Commands will use this to access `MainWindow`,
                `GuiState`, and `Labels`.
            params: Dictionary of any params for command.
        """
        params = params or dict()

        if hasattr(self, "ask_and_do"):
            self.ask_and_do(context, params)
        else:
            okay = self.ask(context, params)
            if okay:
                self.do_with_signal(context, params)

    @staticmethod
    def ask(context: "CommandContext", params: dict) -> bool:
        """Method for information gathering.

        Returns:
            Whether to perform action. By default returns True, but this is
            where we should return False if we prompt user for confirmation
            and they abort.
        """
        return True

    @staticmethod
    def do_action(context: "CommandContext", params: dict):
        """Method for performing action."""
        pass

    @classmethod
    def do_with_signal(cls, context: "CommandContext", params: dict):
        """Wrapper to perform action and notify/track changes.

        Don't override this method!
        """
        cls.do_action(context, params)
        if cls.topics:
            context.signal_update(cls.topics)
        if cls.does_edits:
            context.changestack_push(cls.__name__)


@attr.s(auto_attribs=True)
class FakeApp(object):
    labels: Labels


@attr.s(auto_attribs=True, eq=False)
class CommandContext(object):
    """
    Context within in which commands are executed.

    Attributes:
        state: The `GuiState` object used to store state and pass messages.
        app: The `MainWindow`, available for commands that modify the app.
        update_callback: A callback to receive update notifications.
            This function should accept a list of `UpdateTopic` items.
    """

    state: GuiState
    app: "MainWindow"

    update_callback: Optional[Callable] = None
    _change_stack: List = attr.ib(default=attr.Factory(list))

    @classmethod
    def from_labels(cls, labels: Labels):
        state = GuiState()
        app = FakeApp(labels)
        return cls(state=state, app=app)

    @property
    def labels(self):
        """Alias to app.labels."""
        return self.app.labels

    def signal_update(self, what: List[UpdateTopic]):
        """Calls the update callback after data has been changed."""
        if callable(self.update_callback):
            self.update_callback(what)

    def changestack_push(self, change: str = ""):
        """Adds to stack of changes made by user."""
        # Currently the change doesn't store any data, and we're only using this
        # to determine if there are unsaved changes. Eventually we could use this
        # to support undo/redo.
        self._change_stack.append(change)
        # print(len(self._change_stack))
        self.state["has_changes"] = True

    def changestack_savepoint(self):
        """Marks that project was just saved."""
        self.changestack_push("SAVE")
        self.state["has_changes"] = False

    def changestack_clear(self):
        """Clears stack of changes."""
        self._change_stack = list()
        self.state["has_changes"] = False

    def execute(self, command: Type[AppCommand], **kwargs):
        """Execute command in this context, passing named arguments."""
        command().execute(context=self, params=kwargs)

    # File commands

    def newProject(self):
        """Create a new project in a new window."""
        self.execute(NewProject)

    def openProject(self, first_open: bool = False):
        """
        Allows use to select and then open a saved project.

        Args:
            first_open: Whether this is the first window opened. If True,
                then the new project is loaded into the current window
                rather than a new application window.

        Returns:
            None.
        """
        self.execute(OpenProject, first_open=first_open)

    def importDPK(self):
        """Imports DeepPoseKit datasets."""
        self.execute(ImportDeepPoseKit)

    def importCoco(self):
        """Imports COCO datasets."""
        self.execute(ImportCoco)

    def importDLC(self):
        """Imports DeepLabCut datasets."""
        self.execute(ImportDeepLabCut)

    def importLEAP(self):
        """Imports LEAP matlab datasets."""
        self.execute(ImportLEAP)

    def saveProject(self):
        """Show gui to save project (or save as if not yet saved)."""
        self.execute(SaveProject)

    def saveProjectAs(self):
        """Show gui to save project as a new file."""
        self.execute(SaveProjectAs)

    def exportAnalysisFile(self):
        """Shows gui for exporting analysis h5 file."""
        self.execute(ExportAnalysisFile)

    def exportLabeledClip(self):
        """Shows gui for exporting clip with visual annotations."""
        self.execute(ExportLabeledClip)

    def exportLabeledFrames(self):
        """Gui for exporting the training dataset of labels/frame images."""
        self.execute(ExportLabeledFrames)

    # Navigation Commands

    def previousLabeledFrame(self):
        """Goes to labeled frame prior to current frame."""
        self.execute(GoPreviousLabeledFrame)

    def nextLabeledFrame(self):
        """Goes to labeled frame after current frame."""
        self.execute(GoNextLabeledFrame)

    def nextUserLabeledFrame(self):
        """Goes to next labeled frame with user instances."""
        self.execute(GoNextUserLabeledFrame)

    def nextSuggestedFrame(self):
        """Goes to next suggested frame."""
        self.execute(GoNextSuggestedFrame)

    def prevSuggestedFrame(self):
        """Goes to previous suggested frame."""
        self.execute(GoPrevSuggestedFrame)

    def nextTrackFrame(self):
        """Goes to next frame on which a track starts."""
        self.execute(GoNextTrackFrame)

    def gotoFrame(self):
        """Shows gui to go to frame by number."""
        self.execute(GoFrameGui)

    def selectToFrame(self):
        """Shows gui to go to frame by number."""
        self.execute(SelectToFrameGui)

    def gotoVideoAndFrame(self, video: Video, frame_idx: int):
        """Activates video and goes to frame."""
        NavCommand.go_to(self, frame_idx, video)

    # Editing Commands

    def addVideo(self):
        """Shows gui for adding videos to project."""
        self.execute(AddVideo)

    def replaceVideo(self):
        """Shows gui for replacing videos to project."""
        self.execute(ReplaceVideo)

    def removeVideo(self):
        """Removes selected video from project."""
        self.execute(RemoveVideo)

    def openSkeleton(self):
        """Shows gui for loading saved skeleton into project."""
        self.execute(OpenSkeleton)

    def saveSkeleton(self):
        """Shows gui for saving skeleton from project."""
        self.execute(SaveSkeleton)

    def newNode(self):
        """Adds new node to skeleton."""
        self.execute(NewNode)

    def deleteNode(self):
        """Removes (currently selected) node from skeleton."""
        self.execute(DeleteNode)

    def setNodeName(self, skeleton, node, name):
        """Changes name of node in skeleton."""
        self.execute(SetNodeName, skeleton=skeleton, node=node, name=name)

    def setNodeSymmetry(self, skeleton, node, symmetry: str):
        """Sets node symmetry in skeleton."""
        self.execute(SetNodeSymmetry, skeleton=skeleton, node=node, symmetry=symmetry)

    def updateEdges(self):
        """Called when edges in skeleton have been changed."""
        self.signal_update([UpdateTopic.skeleton])

    def newEdge(self, src_node, dst_node):
        """Adds new edge to skeleton."""
        self.execute(NewEdge, src_node=src_node, dst_node=dst_node)

    def deleteEdge(self):
        """Removes (currently selected) edge from skeleton."""
        self.execute(DeleteEdge)

    def deletePredictions(self):
        """Deletes all predicted instances in project."""
        self.execute(DeleteAllPredictions)

    def deleteFramePredictions(self):
        """Deletes all predictions on current frame."""
        self.execute(DeleteFramePredictions)

    def deleteClipPredictions(self):
        """Deletes all predictions within selected range of video frames."""
        self.execute(DeleteClipPredictions)

    def deleteAreaPredictions(self):
        """Gui for deleting instances within some rect on frame images."""
        self.execute(DeleteAreaPredictions)

    def deleteLowScorePredictions(self):
        """Gui for deleting instances below some score threshold."""
        self.execute(DeleteLowScorePredictions)

    def deleteFrameLimitPredictions(self):
        """Gui for deleting instances beyond some number in each frame."""
        self.execute(DeleteFrameLimitPredictions)

    def completeInstanceNodes(self, instance: Instance):
        """Adds missing nodes to given instance."""
        self.execute(AddMissingInstanceNodes, instance=instance)

    def newInstance(
        self,
        copy_instance: Optional[Instance] = None,
        init_method: str = "best",
        location: Optional[QtCore.QPoint] = None,
        mark_complete: bool = False,
    ):
        """
        Creates a new instance, copying node coordinates as appropriate.

        Args:
            copy_instance: The :class:`Instance` (or
                :class:`PredictedInstance`) which we want to copy.
            init_method: Method to use for positioning nodes.
            location: The location where instance should be added (if node init
                method supports custom location).
        """
        self.execute(
            AddInstance,
            copy_instance=copy_instance,
            init_method=init_method,
            location=location,
            mark_complete=mark_complete,
        )

    def setPointLocations(
        self, instance: Instance, nodes_locations: Dict["Node", Tuple[int, int]]
    ):
        """Sets locations for node(s) for an instance."""
        self.execute(
            SetInstancePointLocations,
            instance=instance,
            nodes_locations=nodes_locations,
        )

    def setInstancePointVisibility(
        self, instance: Instance, node: "Node", visible: bool
    ):
        """Toggles visibility set for a node for an instance."""
        self.execute(
            SetInstancePointVisibility, instance=instance, node=node, visible=visible
        )

    def deleteSelectedInstance(self):
        """Deletes currently selected instance."""
        self.execute(DeleteSelectedInstance)

    def deleteSelectedInstanceTrack(self):
        """Deletes all instances from track of currently selected instance."""
        self.execute(DeleteSelectedInstanceTrack)

    def deleteDialog(self):
        """Deletes using options selected in a dialog."""
        self.execute(DeleteDialogCommand)

    def addTrack(self):
        """Creates new track and moves selected instance into this track."""
        self.execute(AddTrack)

    def setInstanceTrack(self, new_track: "Track"):
        """Sets track for selected instance."""
        self.execute(SetSelectedInstanceTrack, new_track=new_track)

    def setTrackName(self, track: "Track", name: str):
        """Sets name for track."""
        self.execute(SetTrackName, track=track, name=name)

    def transposeInstance(self):
        """Transposes tracks for two instances.

        If there are only two instances, then this swaps tracks.
        Otherwise, it allows user to select the instances for which we want
        to swap tracks.
        """
        self.execute(TransposeInstances)

    def importPredictions(self):
        """Starts gui for importing another dataset into currently one."""
        self.execute(MergeProject)

    def generateSuggestions(self, params: Dict):
        """Generates suggestions using given params dictionary."""
        self.execute(GenerateSuggestions, **params)


# File Commands


class NewProject(AppCommand):
    @staticmethod
    def do_action(context: CommandContext, params: dict):
        window = context.app.__class__()
        window.showMaximized()


class OpenProject(AppCommand):
    @staticmethod
    def do_action(context: "CommandContext", params: dict):
        filename = params["filename"]
        if OPEN_IN_NEW and not params.get("first_open", False):
            new_window = context.app.__class__()
            new_window.showMaximized()
            new_window.loadProjectFile(filename)
        else:
            context.app.loadProjectFile(filename)

    @staticmethod
    def ask(context: "CommandContext", params: dict) -> bool:
        filters = [
            "SLEAP HDF5 dataset (*.slp *.h5 *.hdf5)",
            "JSON labels (*.json *.json.zip)",
        ]

        filename, selected_filter = FileDialog.open(
            context.app,
            dir=None,
            caption="Import labeled data...",
            filter=";;".join(filters),
        )

        if len(filename) == 0:
            return False

        params["filename"] = filename
        return True


class ImportDeepPoseKit(AppCommand):
    @staticmethod
    def do_action(context: "CommandContext", params: dict):

        labels = Labels.from_deepposekit(
            filename=params["filename"],
            video_path=params["video_path"],
            skeleton_path=params["skeleton_path"],
        )

        new_window = context.app.__class__()
        new_window.showMaximized()
        new_window.loadLabelsObject(labels=labels)

    @staticmethod
    def ask(context: "CommandContext", params: dict) -> bool:
        filters = ["HDF5 (*.h5 *.hdf5)"]

        filename, selected_filter = FileDialog.open(
            context.app,
            dir=None,
            caption="Import DeepPoseKit dataset...",
            filter=";;".join(filters),
        )

        if len(filename) == 0:
            return False

        file_dir = os.path.dirname(filename)
        paths = [
            os.path.join(file_dir, "video.mp4"),
            os.path.join(file_dir, "skeleton.csv"),
        ]

        missing = [not os.path.exists(path) for path in paths]

        if sum(missing):
            okay = MissingFilesDialog(filenames=paths, missing=missing).exec_()

            if not okay or sum(missing):
                return False

        params["filename"] = filename
        params["video_path"] = paths[0]
        params["skeleton_path"] = paths[1]

        return True


class ImportLEAP(AppCommand):
    @staticmethod
    def do_action(context: "CommandContext", params: dict):

        labels = Labels.load_leap_matlab(filename=params["filename"],)

        new_window = context.app.__class__()
        new_window.showMaximized()
        new_window.loadLabelsObject(labels=labels)

    @staticmethod
    def ask(context: "CommandContext", params: dict) -> bool:
        filters = ["Matlab (*.mat)"]

        filename, selected_filter = FileDialog.open(
            context.app,
            dir=None,
            caption="Import LEAP Matlab dataset...",
            filter=";;".join(filters),
        )

        if len(filename) == 0:
            return False

        params["filename"] = filename

        return True


class ImportCoco(AppCommand):
    @staticmethod
    def do_action(context: "CommandContext", params: dict):

        labels = Labels.load_coco(
            filename=params["filename"], img_dir=params["img_dir"], use_missing_gui=True
        )

        new_window = context.app.__class__()
        new_window.showMaximized()
        new_window.loadLabelsObject(labels=labels)

    @staticmethod
    def ask(context: "CommandContext", params: dict) -> bool:
        filters = ["JSON (*.json)"]

        filename, selected_filter = FileDialog.open(
            context.app,
            dir=None,
            caption="Import COCO dataset...",
            filter=";;".join(filters),
        )

        if len(filename) == 0:
            return False

        # QtWidgets.QMessageBox(
        #     text="Please locate the directory with image files for this dataset."
        # ).exec_()
        #
        # img_dir = FileDialog.openDir(
        #     None,
        #     directory=os.path.dirname(filename),
        #     caption="Open Image Directory"
        # )
        # if len(img_dir) == 0:
        #     return False

        params["filename"] = filename
        params["img_dir"] = os.path.dirname(filename)

        return True


class ImportDeepLabCut(AppCommand):
    @staticmethod
    def do_action(context: "CommandContext", params: dict):

        labels = Labels.load_deeplabcut_csv(filename=params["filename"])

        new_window = context.app.__class__()
        new_window.showMaximized()
        new_window.loadLabelsObject(labels=labels)

    @staticmethod
    def ask(context: "CommandContext", params: dict) -> bool:
        filters = ["CSV (*.csv)"]

        filename, selected_filter = FileDialog.open(
            context.app,
            dir=None,
            caption="Import DeepLabCut dataset...",
            filter=";;".join(filters),
        )

        if len(filename) == 0:
            return False

        params["filename"] = filename

        return True


class SaveProjectAs(AppCommand):
    @staticmethod
    def _try_save(context, labels: Labels, filename: str):
        """Helper function which attempts save and handles errors."""
        success = False
        try:
            Labels.save_file(labels=labels, filename=filename)
            success = True
            # Mark savepoint in change stack
            context.changestack_savepoint()

        except Exception as e:
            message = f"An error occured when attempting to save:\n {e}\n\n"
            message += "Try saving your project with a different filename or in a different format."
            QtWidgets.QMessageBox(text=message).exec_()

        # Redraw. Not sure why, but sometimes we need to do this.
        context.app.plotFrame()

        return success

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        if cls._try_save(context, context.state["labels"], params["filename"]):
            # If save was successful
            context.state["filename"] = params["filename"]

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        default_name = context.state["filename"] or "untitled"
        p = PurePath(default_name)
        default_name = str(p.with_name(f"{p.stem} copy{p.suffix}"))

        filters = [
            "SLEAP HDF5 dataset (*.slp)",
            "SLEAP JSON dataset (*.json)",
            "Compressed JSON (*.zip)",
        ]
        filename, selected_filter = FileDialog.save(
            context.app,
            caption="Save As...",
            dir=default_name,
            filter=";;".join(filters),
        )

        if len(filename) == 0:
            return False

        params["filename"] = filename
        return True


class ExportAnalysisFile(AppCommand):
    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        from sleap.info.write_tracking_h5 import main as write_analysis

        write_analysis(
            context.labels, output_path=params["output_path"], all_frames=True
        )

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        default_name = context.state["filename"] or "untitled"
        p = PurePath(default_name)
        default_name = str(p.with_name(f"{p.stem}.analysis.h5"))

        filename, selected_filter = FileDialog.save(
            context.app,
            caption="Export Analysis File...",
            dir=default_name,
            filter="SLEAP Analysis HDF5 (*.h5)",
        )

        if len(filename) == 0:
            return False

        params["output_path"] = filename
        return True


class SaveProject(SaveProjectAs):
    @classmethod
    def ask(cls, context: CommandContext, params: dict) -> bool:
        if context.state["filename"] is not None:
            params["filename"] = context.state["filename"]
            return True

        # No filename (must be new project), so treat as "Save as"
        return SaveProjectAs.ask(context, params)


class ExportLabeledClip(AppCommand):
    @staticmethod
    def do_action(context: CommandContext, params: dict):
        from sleap.io.visuals import save_labeled_video

        save_labeled_video(
            filename=params["filename"],
            labels=context.state["labels"],
            video=context.state["video"],
            frames=list(range(*context.state["frame_range"])),
            fps=params["fps"],
            gui_progress=True,
        )

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        if context.state["has_frame_range"]:

            fps, okay = QtWidgets.QInputDialog.getInt(
                context.app,
                "Frames per second",
                "Frames per second:",
                getattr(context.state["video"], "fps", 30),
                1,
                300,
            )
            if not okay:
                return False

            filename, _ = FileDialog.save(
                context.app,
                caption="Save Video As...",
                dir=context.state["filename"] + ".avi",
                filter="AVI Video (*.avi)",
            )

            if len(filename) == 0:
                return False

            params["filename"] = filename
            params["fps"] = fps
            return True
        else:
            message = (
                "There is no selected clip. You can select a clip by "
                "shift-dragging over the range of frames in the video "
                "seekbar."
            )
            QMessageBox(text=message).exec_()
            return False


class ExportLabeledFrames(AppCommand):
    @staticmethod
    def do_action(context: CommandContext, params: dict):
        Labels.save_file(
            context.state["labels"],
            params["filename"],
            default_suffix="slp",
            save_frame_data=True,
        )

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        filters = [
            "SLEAP HDF5 dataset (*.slp *.h5)",
            "Compressed JSON dataset (*.json *.json.zip)",
        ]
        filename, _ = FileDialog.save(
            context.app,
            caption="Save Labeled Frames As...",
            dir=context.state["filename"] + ".slp",
            filters=";;".join(filters),
        )
        if len(filename) == 0:
            return False

        params["filename"] = filename
        return True


# Navigation Commands


class GoIteratorCommand(AppCommand):
    @staticmethod
    def _plot_if_next(context, frame_iterator: Iterator) -> bool:
        """Plots next frame (if there is one) from iterator.

        Arguments:
            frame_iterator: The iterator from which we'll try to get next
            :class:`LabeledFrame`.

        Returns:
            True if we went to next frame.
        """
        try:
            next_lf = next(frame_iterator)
        except StopIteration:
            return False

        context.state["frame_idx"] = next_lf.frame_idx
        return True

    @staticmethod
    def _get_frame_iterator(context: CommandContext):
        raise NotImplementedError("Call to virtual method.")

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        frames = cls._get_frame_iterator(context)
        cls._plot_if_next(context, frames)


class GoPreviousLabeledFrame(GoIteratorCommand):
    @staticmethod
    def _get_frame_iterator(context: CommandContext):
        return context.labels.frames(
            context.state["video"],
            from_frame_idx=context.state["frame_idx"],
            reverse=True,
        )


class GoNextLabeledFrame(GoIteratorCommand):
    @staticmethod
    def _get_frame_iterator(context: CommandContext):
        return context.labels.frames(
            context.state["video"], from_frame_idx=context.state["frame_idx"]
        )


class GoNextUserLabeledFrame(GoIteratorCommand):
    @staticmethod
    def _get_frame_iterator(context: CommandContext):
        frames = context.labels.frames(
            context.state["video"], from_frame_idx=context.state["frame_idx"]
        )
        # Filter to frames with user instances
        frames = filter(lambda lf: lf.has_user_instances, frames)
        return frames


class NavCommand(AppCommand):
    @staticmethod
    def go_to(context, frame_idx: int, video: Optional[Video] = None):
        if video is not None:
            context.state["video"] = video
        context.state["frame_idx"] = frame_idx


class GoNextSuggestedFrame(NavCommand):
    seek_direction = 1

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):

        next_suggestion_frame = context.labels.get_next_suggestion(
            context.state["video"], context.state["frame_idx"], cls.seek_direction
        )
        if next_suggestion_frame is not None:
            cls.go_to(
                context, next_suggestion_frame.frame_idx, next_suggestion_frame.video
            )
            selection_idx = context.labels.get_suggestions().index(
                next_suggestion_frame
            )
            context.state["suggestion_idx"] = selection_idx


class GoPrevSuggestedFrame(GoNextSuggestedFrame):
    seek_direction = -1


class GoNextTrackFrame(NavCommand):
    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        video = context.state["video"]
        cur_idx = context.state["frame_idx"]
        track_ranges = context.labels.get_track_occupancy(video)

        later_tracks = [
            (track_range.start, track)
            for track, track_range in track_ranges.items()
            if track_range.start is not None and track_range.start > cur_idx
        ]

        later_tracks.sort(key=operator.itemgetter(0))

        if later_tracks:
            next_idx, next_track = later_tracks[0]
            cls.go_to(context, next_idx)

            # Select the instance in the new track
            lf = context.labels.find(video, next_idx, return_new=True)[0]
            track_instances = [
                inst for inst in lf.instances_to_show if inst.track == next_track
            ]
            if track_instances:
                context.state["instance"] = track_instances[0]


class GoFrameGui(NavCommand):
    @classmethod
    def do_action(cls, context: "CommandContext", params: dict):
        cls.go_to(context, params["frame_idx"])

    @classmethod
    def ask(cls, context: "CommandContext", params: dict) -> bool:
        frame_number, okay = QtWidgets.QInputDialog.getInt(
            context.app,
            "Go To Frame...",
            "Frame Number:",
            context.state["frame_idx"] + 1,
            1,
            context.state["video"].frames,
        )
        params["frame_idx"] = frame_number - 1

        return okay


class SelectToFrameGui(NavCommand):
    @classmethod
    def do_action(cls, context: "CommandContext", params: dict):
        context.app.player.setSeekbarSelection(
            params["from_frame_idx"], params["to_frame_idx"]
        )

    @classmethod
    def ask(cls, context: "CommandContext", params: dict) -> bool:
        frame_number, okay = QtWidgets.QInputDialog.getInt(
            context.app,
            "Select To Frame...",
            "Frame Number:",
            context.state["frame_idx"] + 1,
            1,
            context.state["video"].frames,
        )
        params["from_frame_idx"] = context.state["frame_idx"]
        params["to_frame_idx"] = frame_number - 1

        return okay


# Editing Commands


class EditCommand(AppCommand):
    """Class for commands which change data in project."""

    does_edits = True


class AddVideo(EditCommand):
    topics = [UpdateTopic.video]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        import_list = params["import_list"]

        video = None
        for import_item in import_list:
            # Create Video object
            video = Video.from_filename(**import_item["params"])
            # Add to labels
            context.labels.add_video(video)
            context.changestack_push("add video")

        # Load if no video currently loaded
        if context.state["video"] is None:
            context.state["video"] = video

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        """Shows gui for adding video to project."""
        params["import_list"] = ImportVideos().go()

        return len(params["import_list"]) > 0


class ReplaceVideo(EditCommand):
    topics = [UpdateTopic.video]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        new_paths = params["new_video_paths"]

        for video, new_path in zip(context.labels.videos, new_paths):
            if new_path != video.backend.filename:
                video.backend.filename = new_path
                video.backend.reset()

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        """Shows gui for replacing videos in project."""
        paths = [video.backend.filename for video in context.labels.videos]

        okay = MissingFilesDialog(filenames=paths, replace=True).exec_()

        if not okay:
            return False

        params["new_video_paths"] = paths

        return True


class RemoveVideo(EditCommand):
    topics = [UpdateTopic.video]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        video = params["video"]
        # Remove video
        context.labels.remove_video(video)

        # Update view if this was the current video
        if context.state["video"] == video:
            if len(context.labels.videos) > 0:
                context.state["video"] = context.labels.videos[-1]
            else:
                context.state["video"] = None

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        video = context.state["selected_video"]
        if video is None:
            return False

        # Count labeled frames for this video
        n = len(context.labels.find(video))

        # Warn if there are labels that will be deleted
        if n > 0:
            response = QMessageBox.critical(
                context.app,
                "Removing video with labels",
                f"{n} labeled frames in this video will be deleted, "
                "are you sure you want to remove this video?",
                QMessageBox.Yes,
                QMessageBox.No,
            )
            if response == QMessageBox.No:
                return False

        params["video"] = video
        return True


class OpenSkeleton(EditCommand):
    topics = [UpdateTopic.skeleton]

    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        filters = ["JSON skeleton (*.json)", "HDF5 skeleton (*.h5 *.hdf5)"]
        filename, selected_filter = FileDialog.open(
            context.app, dir=None, caption="Open skeleton...", filter=";;".join(filters)
        )

        if len(filename) == 0:
            return False

        params["filename"] = filename
        return True

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        filename = params["filename"]
        if filename.endswith(".json"):
            context.state["skeleton"] = Skeleton.load_json(filename)
        elif filename.endswith((".h5", ".hdf5")):
            sk_list = Skeleton.load_all_hdf5(filename)
            if len(sk_list):
                context.state["skeleton"] = sk_list[0]

        if context.state["skeleton"] not in context.labels:
            context.labels.skeletons.append(context.state["skeleton"])


class SaveSkeleton(AppCommand):
    @staticmethod
    def ask(context: CommandContext, params: dict) -> bool:
        default_name = "skeleton.json"
        filters = ["JSON skeleton (*.json)", "HDF5 skeleton (*.h5 *.hdf5)"]
        filename, selected_filter = FileDialog.save(
            context.app,
            caption="Save As...",
            dir=default_name,
            filter=";;".join(filters),
        )

        if len(filename) == 0:
            return False

        params["filename"] = filename
        return True

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        filename = params["filename"]
        if filename.endswith(".json"):
            context.state["skeleton"].save_json(filename)
        elif filename.endswith((".h5", ".hdf5")):
            context.state["skeleton"].save_hdf5(filename)


class NewNode(EditCommand):
    topics = [UpdateTopic.skeleton]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        # Find new part name
        part_name = "new_part"
        i = 1
        while part_name in context.state["skeleton"]:
            part_name = f"new_part_{i}"
            i += 1

        # Add the node to the skeleton
        context.state["skeleton"].add_node(part_name)


class DeleteNode(EditCommand):
    topics = [UpdateTopic.skeleton]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        node = context.state["selected_node"]
        context.state["skeleton"].delete_node(node)


class SetNodeName(EditCommand):
    topics = [UpdateTopic.skeleton]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        node = params["node"]
        name = params["name"]
        skeleton = params["skeleton"]
        skeleton.relabel_node(node.name, name)


class SetNodeSymmetry(EditCommand):
    topics = [UpdateTopic.skeleton]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        node = params["node"]
        symmetry = params["symmetry"]
        skeleton = params["skeleton"]

        if symmetry:
            skeleton.add_symmetry(node, symmetry)
        else:
            # Value was cleared by user, so delete symmetry
            symmetric_to = skeleton.get_symmetry(node)
            skeleton.delete_symmetry(node, symmetric_to)


class NewEdge(EditCommand):
    topics = [UpdateTopic.skeleton]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        src_node = params["src_node"]
        dst_node = params["dst_node"]

        # Check if they're in the graph
        if (
            src_node not in context.state["skeleton"]
            or dst_node not in context.state["skeleton"]
        ):
            return

        # Add edge
        context.state["skeleton"].add_edge(source=src_node, destination=dst_node)


class DeleteEdge(EditCommand):
    topics = [UpdateTopic.skeleton]

    @staticmethod
    def ask(context: "CommandContext", params: dict) -> bool:
        params["edge"] = context.state["selected_edge"]
        return True

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        edge = params["edge"]
        # Delete edge
        context.state["skeleton"].delete_edge(**edge)


class InstanceDeleteCommand(EditCommand):
    topics = [UpdateTopic.project_instances]

    @staticmethod
    def get_frame_instance_list(context: CommandContext, params: dict):
        raise NotImplementedError("Call to virtual method.")

    @staticmethod
    def _confirm_deletion(context: CommandContext, lf_inst_list: List) -> bool:
        """Helper function to confirm before deleting instances.

        Args:
            lf_inst_list: A list of (labeled frame, instance) tuples.
        """

        title = "Deleting instances"
        message = (
            f"There are {len(lf_inst_list)} instances which "
            f"would be deleted. Are you sure you want to delete these?"
        )

        # Confirm that we want to delete
        resp = QMessageBox.critical(
            context.app, title, message, QMessageBox.Yes, QMessageBox.No
        )

        if resp == QMessageBox.No:
            return False

        return True

    @staticmethod
    def _do_deletion(context: CommandContext, lf_inst_list: List[int]):
        # Delete the instances
        for lf, inst in lf_inst_list:
            context.labels.remove_instance(lf, inst, in_transaction=True)

        # Update caches since we skipped doing this after each deletion
        context.labels.update_cache()

        # Update visuals
        context.changestack_push("delete instances")

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        cls._do_deletion(context, params["lf_instance_list"])

    @classmethod
    def ask(cls, context: CommandContext, params: dict) -> bool:
        lf_inst_list = cls.get_frame_instance_list(context, params)
        params["lf_instance_list"] = lf_inst_list

        return cls._confirm_deletion(context, lf_inst_list)


class DeleteAllPredictions(InstanceDeleteCommand):
    @staticmethod
    def get_frame_instance_list(
        context: CommandContext, params: dict
    ) -> List[Tuple[LabeledFrame, Instance]]:
        return [
            (lf, inst)
            for lf in context.labels
            for inst in lf
            if type(inst) == PredictedInstance
        ]


class DeleteFramePredictions(InstanceDeleteCommand):
    @staticmethod
    def _confirm_deletion(self, *args, **kwargs):
        # Don't require confirmation when deleting from current frame
        return True

    @staticmethod
    def get_frame_instance_list(context: CommandContext, params: dict):
        predicted_instances = [
            (lf, inst)
            for lf in context.labels.find(
                context.state["video"], frame_idx=context.state["frame_idx"]
            )
            for inst in lf
            if type(inst) == PredictedInstance
        ]

        return predicted_instances


class DeleteClipPredictions(InstanceDeleteCommand):
    @staticmethod
    def get_frame_instance_list(context: CommandContext, params: dict):
        predicted_instances = [
            (lf, inst)
            for lf in context.labels.find(
                context.state["video"], frame_idx=range(*context.state["frame_range"])
            )
            for inst in lf
            if type(inst) == PredictedInstance
        ]
        return predicted_instances


class DeleteAreaPredictions(InstanceDeleteCommand):
    @staticmethod
    def get_frame_instance_list(context: CommandContext, params: dict):
        min_corner = params["min_corner"]
        max_corner = params["max_corner"]

        def is_bounded(inst):
            points_array = inst.points_array
            valid_points = points_array[~np.isnan(points_array).any(axis=1)]

            is_gt_min = np.all(valid_points >= min_corner)
            is_lt_max = np.all(valid_points <= max_corner)
            return is_gt_min and is_lt_max

        # Find all instances contained in selected area
        predicted_instances = [
            (lf, inst)
            for lf in context.labels.find(context.state["video"])
            for inst in lf
            if type(inst) == PredictedInstance and is_bounded(inst)
        ]

        return predicted_instances

    @classmethod
    def ask_and_do(cls, context: CommandContext, params: dict):
        # Callback to delete after area has been selected
        def delete_area_callback(x0, y0, x1, y1):
            context.app.updateStatusMessage()

            # Make sure there was an area selected
            if x0 == x1 or y0 == y1:
                return

            params["min_corner"] = (x0, y0)
            params["max_corner"] = (x1, y1)

            predicted_instances = cls.get_frame_instance_list(context, params)

            if cls._confirm_deletion(context, predicted_instances):
                params["lf_instance_list"] = predicted_instances
                cls.do_with_signal(context, params)

        # Prompt the user to select area
        context.app.updateStatusMessage(
            f"Please select the area from which to remove instances. This will be applied to all frames."
        )
        context.app.player.onAreaSelection(delete_area_callback)


class DeleteLowScorePredictions(InstanceDeleteCommand):
    @staticmethod
    def get_frame_instance_list(context: CommandContext, params: dict):
        score_thresh = params["score_threshold"]
        predicted_instances = [
            (lf, inst)
            for lf in context.labels.find(context.state["video"])
            for inst in lf
            if type(inst) == PredictedInstance and inst.score < score_thresh
        ]
        return predicted_instances

    @classmethod
    def ask(cls, context: CommandContext, params: dict) -> bool:
        score_thresh, okay = QtWidgets.QInputDialog.getDouble(
            context.app, "Delete Instances with Low Score...", "Score Below:", 1, 0, 100
        )
        if okay:
            params["score_threshold"] = score_thresh
            return super().ask(context, params)


class DeleteFrameLimitPredictions(InstanceDeleteCommand):
    @staticmethod
    def get_frame_instance_list(context: CommandContext, params: dict):
        count_thresh = params["count_threshold"]
        predicted_instances = []
        # Find all instances contained in selected area
        for lf in context.labels.find(context.state["video"]):
            if len(lf.predicted_instances) > count_thresh:
                # Get all but the count_thresh many instances with the highest score
                extra_instances = sorted(
                    lf.predicted_instances, key=operator.attrgetter("score")
                )[:-count_thresh]
                predicted_instances.extend([(lf, inst) for inst in extra_instances])
        return predicted_instances

    @classmethod
    def ask(cls, context: CommandContext, params: dict) -> bool:
        count_thresh, okay = QtWidgets.QInputDialog.getInt(
            context.app,
            "Limit Instances in Frame...",
            "Maximum instances in a frame:",
            3,
            1,
            100,
        )
        if okay:
            params["count_threshold"] = count_thresh
            return super().ask(context, params)


class TransposeInstances(EditCommand):
    topics = [UpdateTopic.project_instances, UpdateTopic.tracks]

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        instances = params["instances"]

        if len(instances) != 2:
            return

        # Swap tracks for current and subsequent frames when we have tracks
        old_track, new_track = instances[0].track, instances[1].track
        if old_track is not None and new_track is not None:
            frame_range = (context.state["frame_idx"], context.state["video"].frames)
            context.labels.track_swap(
                context.state["video"], new_track, old_track, frame_range
            )

    @classmethod
    def ask_and_do(cls, context: CommandContext, params: dict):
        def on_each(instances: list):
            word = "next" if len(instances) else "first"
            context.app.updateStatusMessage(
                f"Please select the {word} instance to transpose..."
            )

        def on_success(instances: list):
            params["instances"] = instances
            cls.do_with_signal(context, params)

        if len(context.state["labeled_frame"].instances) < 2:
            return
        # If there are just two instances, transpose them.
        if len(context.state["labeled_frame"].instances) == 2:
            params["instances"] = context.state["labeled_frame"].instances
            cls.do_with_signal(context, params)
        # If there are more than two, then we need the user to select the instances.
        else:
            context.app.player.onSequenceSelect(
                seq_len=2,
                on_success=on_success,
                on_each=on_each,
                on_failure=lambda x: context.app.updateStatusMessage(),
            )


class DeleteSelectedInstance(EditCommand):
    topics = [UpdateTopic.frame, UpdateTopic.project_instances, UpdateTopic.suggestions]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        selected_inst = context.state["instance"]
        if selected_inst is None:
            return

        context.labels.remove_instance(context.state["labeled_frame"], selected_inst)


class DeleteSelectedInstanceTrack(EditCommand):
    topics = [
        UpdateTopic.project_instances,
        UpdateTopic.tracks,
        UpdateTopic.suggestions,
    ]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        selected_inst = context.state["instance"]
        if selected_inst is None:
            return

        track = selected_inst.track
        context.labels.remove_instance(context.state["labeled_frame"], selected_inst)

        if track is not None:
            # remove any instance on this track
            for lf in context.labels.find(context.state["video"]):
                track_instances = filter(lambda inst: inst.track == track, lf.instances)
                for inst in track_instances:
                    context.labels.remove_instance(lf, inst)


class DeleteDialogCommand(EditCommand):
    topics = [
        UpdateTopic.project_instances,
    ]

    @staticmethod
    def ask_and_do(context: CommandContext, params: dict):
        if DeleteDialog(context).exec_():
            context.signal_update([UpdateTopic.project_instances])


class AddTrack(EditCommand):
    topics = [UpdateTopic.tracks]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        track_numbers_used = [
            int(track.name) for track in context.labels.tracks if track.name.isnumeric()
        ]
        next_number = max(track_numbers_used, default=0) + 1
        new_track = Track(spawned_on=context.state["frame_idx"], name=str(next_number))

        context.labels.add_track(context.state["video"], new_track)

        context.execute(SetSelectedInstanceTrack, new_track=new_track)


class SetSelectedInstanceTrack(EditCommand):
    topics = [UpdateTopic.tracks]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        selected_instance = context.state["instance"]
        new_track = params["new_track"]
        if selected_instance is None:
            return

        old_track = selected_instance.track

        # When setting track for an instance that doesn't already have a track set,
        # just set for selected instance.
        if old_track is None:
            # Move anything already in the new track out of it
            new_track_instances = context.labels.find_track_occupancy(
                video=context.state["video"],
                track=new_track,
                frame_range=(
                    context.state["frame_idx"],
                    context.state["frame_idx"] + 1,
                ),
            )
            for instance in new_track_instances:
                instance.track = None
            # Move selected instance into new track
            context.labels.track_set_instance(
                context.state["labeled_frame"], selected_instance, new_track
            )

        # When the instance does already have a track, then we want to update
        # the track for a range of frames.
        else:

            # Determine range that should be affected
            if context.state["has_frame_range"]:
                # If range is selected in seekbar, use that
                frame_range = tuple(context.state["frame_range"])
            else:
                # Otherwise, range is current to last frame
                frame_range = (
                    context.state["frame_idx"],
                    context.state["video"].frames,
                )

            # Do the swap
            context.labels.track_swap(
                context.state["video"], new_track, old_track, frame_range
            )

        # Make sure the originally selected instance is still selected
        context.state["instance"] = selected_instance


class SetTrackName(EditCommand):
    topics = [UpdateTopic.tracks, UpdateTopic.frame]

    @staticmethod
    def do_action(context: CommandContext, params: dict):
        track = params["track"]
        name = params["name"]
        track.name = name


class GenerateSuggestions(EditCommand):
    topics = [UpdateTopic.suggestions]

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):

        win = MessageDialog("Generating list of suggested frames...", context.app)

        new_suggestions = VideoFrameSuggestions.suggest(
            labels=context.labels, params=params
        )

        context.labels.set_suggestions(new_suggestions)

        win.hide()


class MergeProject(EditCommand):
    topics = [UpdateTopic.all]

    @classmethod
    def ask_and_do(cls, context: CommandContext, params: dict):
        filters = [
            "SLEAP HDF5 dataset (*.slp *.h5 *.hdf5)",
            "SLEAP JSON dataset (*.json *.json.zip)",
        ]

        filenames, selected_filter = FileDialog.openMultiple(
            context.app,
            dir=None,
            caption="Import labeled data...",
            filter=";;".join(filters),
        )

        if len(filenames) == 0:
            return

        for filename in filenames:
            gui_video_callback = Labels.make_gui_video_callback(
                search_paths=[os.path.dirname(filename)]
            )

            new_labels = Labels.load_file(filename, video_callback=gui_video_callback)

            # Merging data is handled by MergeDialog
            MergeDialog(base_labels=context.labels, new_labels=new_labels).exec_()

        cls.do_with_signal(context, params)


class AddInstance(EditCommand):
    topics = [UpdateTopic.frame, UpdateTopic.project_instances, UpdateTopic.suggestions]

    @staticmethod
    def get_previous_frame_index(context: CommandContext) -> Optional[int]:
        frames = context.labels.frames(
            context.state["video"],
            from_frame_idx=context.state["frame_idx"],
            reverse=True,
        )

        try:
            next_idx = next(frames).frame_idx
        except:
            return

        return next_idx

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        copy_instance = params.get("copy_instance", None)
        init_method = params.get("init_method", "best")
        location = params.get("location", None)
        mark_complete = params.get("mark_complete", False)

        if context.state["labeled_frame"] is None:
            return

        # FIXME: filter by skeleton type

        from_predicted = copy_instance
        from_prev_frame = False

        if init_method == "best" and copy_instance is None:
            selected_inst = context.state["instance"]
            if selected_inst is not None:
                # If the user has selected an instance, copy that one.
                copy_instance = selected_inst
                from_predicted = copy_instance

        if (
            init_method == "best" and copy_instance is None
        ) or init_method == "prediction":
            unused_predictions = context.state["labeled_frame"].unused_predictions
            if len(unused_predictions):
                # If there are predicted instances that don't correspond to an instance
                # in this frame, use the first predicted instance without matching instance.
                copy_instance = unused_predictions[0]
                from_predicted = copy_instance

        if (
            init_method == "best" and copy_instance is None
        ) or init_method == "prior_frame":
            # Otherwise, if there are instances in previous frames,
            # copy the points from one of those instances.
            prev_idx = cls.get_previous_frame_index(context)

            if prev_idx is not None:
                prev_instances = context.labels.find(
                    context.state["video"], prev_idx, return_new=True
                )[0].instances
                if len(prev_instances) > len(context.state["labeled_frame"].instances):
                    # If more instances in previous frame than current, then use the
                    # first unmatched instance.
                    copy_instance = prev_instances[
                        len(context.state["labeled_frame"].instances)
                    ]
                    from_prev_frame = True
                elif init_method == "best" and (
                    context.state["labeled_frame"].instances
                ):
                    # Otherwise, if there are already instances in current frame,
                    # copy the points from the last instance added to frame.
                    copy_instance = context.state["labeled_frame"].instances[-1]
                elif len(prev_instances):
                    # Otherwise use the last instance added to previous frame.
                    copy_instance = prev_instances[-1]
                    from_prev_frame = True

        from_predicted = from_predicted if hasattr(from_predicted, "score") else None

        # Now create the new instance
        new_instance = Instance(
            skeleton=context.state["skeleton"],
            from_predicted=from_predicted,
            frame=context.state["labeled_frame"],
        )

        has_missing_nodes = False

        # go through each node in skeleton
        for node in context.state["skeleton"].node_names:
            # if we're copying from a skeleton that has this node
            if (
                copy_instance is not None
                and node in copy_instance
                and not copy_instance[node].isnan()
            ):
                # just copy x, y, and visible
                # we don't want to copy a PredictedPoint or score attribute
                new_instance[node] = Point(
                    x=copy_instance[node].x,
                    y=copy_instance[node].y,
                    visible=copy_instance[node].visible,
                    complete=mark_complete,
                )
            else:
                has_missing_nodes = True

        if has_missing_nodes:
            # mark the node as not "visible" if we're copying from a predicted instance without this node
            is_visible = copy_instance is None or (not hasattr(copy_instance, "score"))

            if init_method == "force_directed":
                AddMissingInstanceNodes.add_force_directed_nodes(
                    context=context,
                    instance=new_instance,
                    visible=is_visible,
                    center_point=location,
                )
            elif init_method == "random":
                AddMissingInstanceNodes.add_random_nodes(
                    context=context, instance=new_instance, visible=is_visible
                )
            elif init_method == "template":
                AddMissingInstanceNodes.add_nodes_from_template(
                    context=context,
                    instance=new_instance,
                    visible=is_visible,
                    center_point=location,
                )
            else:
                AddMissingInstanceNodes.add_best_nodes(
                    context=context, instance=new_instance, visible=is_visible
                )

        # If we're copying a predicted instance or from another frame, copy the track
        if hasattr(copy_instance, "score") or from_prev_frame:
            new_instance.track = copy_instance.track

        # Add the instance
        context.labels.add_instance(context.state["labeled_frame"], new_instance)

        if context.state["labeled_frame"] not in context.labels.labels:
            context.labels.append(context.state["labeled_frame"])


class SetInstancePointLocations(EditCommand):
    """Sets locations for node(s) for an instance.

    Note: It's important that this command does *not* update the visual
    scene, since this would redraw the frame and create new visual objects.
    The calling code is responsible for updating the visual scene.

    Params:
        instance: The instance
        nodes_locations: A dictionary of data to set
        * keys are nodes (or node names)
        * values are (x, y) coordinate tuples.
    """

    topics = []

    @classmethod
    def do_action(cls, context: "CommandContext", params: dict):
        instance = params["instance"]
        nodes_locations = params["nodes_locations"]

        for node, (x, y) in nodes_locations.items():
            if node in instance:
                instance[node].x = x
                instance[node].y = y


class SetInstancePointVisibility(EditCommand):
    """Toggles visibility set for a node for an instance.

    Note: It's important that this command does *not* update the visual
    scene, since this would redraw the frame and create new visual objects.
    The calling code is responsible for updating the visual scene.

    Params:
        instance: The instance
        node: The `Node` (or name string)
        visible: Whether to set or clear visibility for node
    """

    topics = []

    @classmethod
    def do_action(cls, context: "CommandContext", params: dict):
        instance = params["instance"]
        node = params["node"]
        visible = params["visible"]

        instance[node].visible = visible


class AddMissingInstanceNodes(EditCommand):
    topics = [UpdateTopic.frame]

    @classmethod
    def do_action(cls, context: CommandContext, params: dict):
        instance = params["instance"]
        visible = params.get("visible", False)

        cls.add_best_nodes(context, instance, visible)

    @classmethod
    def add_best_nodes(cls, context, instance, visible):
        # Try placing missing nodes using a "template" instance
        cls.add_nodes_from_template(context, instance, visible)

        # If the "template" instance has missing nodes (i.e., a node that isn't
        # labeled on any of the instances we used to generate the template),
        # then adding nodes from the template may still result in missing nodes.
        # So we'll use random placement for anything that's still missing.
        cls.add_random_nodes(context, instance, visible)

    @classmethod
    def add_random_nodes(cls, context, instance, visible):
        # the rect that's currently visible in the window view
        in_view_rect = context.app.player.getVisibleRect()

        for node in context.state["skeleton"].nodes:
            if node not in instance.nodes or instance[node].isnan():
                # pick random points within currently zoomed view
                x, y = cls.get_xy_in_rect(in_view_rect)
                # set point for node
                instance[node] = Point(x=x, y=y, visible=visible)

    @staticmethod
    def get_xy_in_rect(rect: QtCore.QRectF):
        """Returns random x, y coordinates within given rect."""
        x = rect.x() + (rect.width() * 0.1) + (np.random.rand() * rect.width() * 0.8)
        y = rect.y() + (rect.height() * 0.1) + (np.random.rand() * rect.height() * 0.8)
        return x, y

    @staticmethod
    def get_rect_center_xy(rect: QtCore.QRectF):
        """Returns x, y at center of rect."""

    @classmethod
    def add_nodes_from_template(
        cls,
        context,
        instance,
        visible: bool = False,
        center_point: QtCore.QPoint = None,
    ):
        from sleap.info import align

        # Get the "template" instance
        template_points = context.labels.get_template_instance_points(
            skeleton=instance.skeleton
        )

        # Align the template on to the current instance with missing points
        if instance.points:
            aligned_template = align.align_instance_points(
                source_points_array=template_points,
                target_points_array=instance.points_array,
            )
        else:
            template_mean = np.nanmean(template_points, axis=0)

            center_point = center_point or context.app.player.getVisibleRect().center()
            center = np.array([center_point.x(), center_point.y()])

            aligned_template = template_points + (center - template_mean)

        # Make missing points from the aligned template
        for i, node in enumerate(instance.skeleton.nodes):
            if node not in instance:
                x, y = aligned_template[i]
                instance[node] = Point(x=x, y=y, visible=visible)

    @classmethod
    def add_force_directed_nodes(
        cls, context, instance, visible, center_point: QtCore.QPoint = None
    ):
        import networkx as nx

        center_point = center_point or context.app.player.getVisibleRect().center()
        center_tuple = (center_point.x(), center_point.y())

        node_positions = nx.spring_layout(
            G=context.state["skeleton"].graph, center=center_tuple, scale=50
        )

        for node, pos in node_positions.items():
            instance[node] = Point(x=pos[0], y=pos[1], visible=visible)
